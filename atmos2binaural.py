#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Dolby TrueHD (.thd) files, Atmos or plain 5.1/7.1, to binaural stereo."""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional

LFE_GAIN_DB = 5.5
LFE_LOWPASS_HZ = 120
# Per preset: share of the HRTF render (the rest amplitude-panned), whether
# every direction is matched to the front level, and the centre, surround and
# height offsets in dB.
PRESETS = {
    "dolby": (0.15, False, (0.0, 0.0, 0.0)),
    "fold-down": (1.0, True, (0.0, -3.0, 0.0)),
}
CEILING_DBTP = -1.0
TMP_DIR_NAME = ".atmos2binaural-tmp"
MOUNT_ENV = "ATMOS2BINAURAL_MOUNT"
DEPS = Path(__file__).resolve().parent / "deps"
PLUGIN = "libharletty_dolby_bridge.dylib" if sys.platform == "darwin" else "libharletty_dolby_bridge.so"
DAMAGE_PATTERN = re.compile(r"Parity check failed|could not be decoded|Parse error|ERROR")
ERROR_LINE = re.compile(r"^\[\S+ ERROR\b")
# Share in percent of the time a track takes per stage, measured on a 279 s
# Atmos track on 13-11-2026.
STAGES = {"checking": 20, "rendering": 45, "measuring": 5, "measuring source": 20, "encoding": 10}


class ConversionError(Exception):
    """A step failed; the message says which and why."""


class Probe(NamedTuple):
    codec: str
    sample_rate: int


class Measurement(NamedTuple):
    true_peak_dbtp: float  # float("-inf") for silence
    loudness_lufs: float


def run(
    command: list[str], feed: Optional[Path] = None, progress: Optional[Callable[[float], None]] = None
) -> subprocess.CompletedProcess:
    """Run `command` without a shell. A non-zero exit raises ConversionError
    with the command and its error output. The file `feed` goes to the
    command's standard input, and `progress` gets the share sent so far."""
    shown = shlex.join(command) + (f" < {shlex.quote(str(feed))}" if feed else "")
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            with subprocess.Popen(
                command, stdin=subprocess.PIPE if feed else subprocess.DEVNULL, stdout=out, stderr=err
            ) as process:
                if feed:
                    send(feed, process.stdin, progress)
            out.seek(0)
            err.seek(0)
            done = subprocess.CompletedProcess(
                command, process.returncode, out.read().decode(errors="replace"), err.read().decode(errors="replace")
            )
    except OSError as e:
        raise ConversionError(f"{shown}: {e}") from e
    if done.returncode != 0:
        raise ConversionError(f"{shown} exited with status {done.returncode}:\n{done.stderr.strip()}")
    return done


def send(path: Path, pipe, progress: Optional[Callable[[float], None]]) -> None:
    """Write the file at `path` to `pipe` in chunks and close it."""
    size = path.stat().st_size or 1
    sent = 0
    try:
        with path.open("rb") as f, pipe:
            while chunk := f.read(1 << 20):
                pipe.write(chunk)
                sent += len(chunk)
                if progress:
                    progress(sent / size)
    except BrokenPipeError:
        pass  # The command stopped reading, and its exit status says why.


class TrackBar:
    """The progress bar of one track while stdout is a terminal: it fills over
    the stages in STAGES. Without a terminal only the closing line is printed."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.live = sys.stdout.isatty()
        self.done = 0
        self.shown: Optional[tuple[int, str]] = None

    def stage(self, name: str) -> Callable[[float], None]:
        start, weight = self.done, STAGES[name]
        self.done += weight

        def show(share: float) -> None:
            percent = int(start + weight * share)
            if not self.live or self.shown == (percent, name):
                return
            self.shown = (percent, name)
            columns = shutil.get_terminal_size().columns
            width = max(10, min(30, columns // 3))
            filled = width * percent // 100
            tail = f" {'█' * filled}{'░' * (width - filled)} {percent:3d}% {name}"
            sys.stdout.write(f"\r\x1b[K{self.label[:max(0, columns - 1 - len(tail))]}{tail}")
            sys.stdout.flush()

        show(0.0)
        return show

    def close(self, line: str) -> None:
        """Replace the bar with the closing line of the track."""
        if self.live:
            sys.stdout.write("\r\x1b[K")
        print(line, flush=True)

    def stop(self) -> None:
        """Keep the bar of a failed track, so the error starts on a new line."""
        if self.shown:
            print(flush=True)


def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="atmos2binaural",
        description="Convert Dolby TrueHD files to binaural stereo FLAC or WAV.",
    )
    parser.add_argument("inputs", nargs="+", metavar="INPUT", help=".thd file or folder of .thd files")
    parser.add_argument("-o", "--output", metavar="OUTPUT_DIR", help="output folder")
    parser.add_argument("-f", "--format", choices=("flac", "wav"), default="flac", help="output format (default flac)")
    parser.add_argument(
        "--hrtf",
        choices=("synthetic", "kemar"),
        default="synthetic",
        help="head model: synthetic (default) or the measured KEMAR head",
    )
    parser.add_argument(
        "--preset",
        choices=tuple(PRESETS),
        default="dolby",
        help="level balance: dolby (default) or fold-down",
    )
    for zone in ("centre", "surround", "height"):
        parser.add_argument(
            f"--{zone}",
            type=float,
            default=0.0,
            metavar="DB",
            help=f"add DB to the {zone} on top of the preset (default 0)",
        )
    parser.add_argument(
        "--allow-damaged",
        action="store_true",
        help="convert damaged streams anyway and report them",
    )
    return parser.parse_args(argv)


def collect_inputs(paths: list[str]) -> list[Path]:
    tracks: list[Path] = []
    for name in paths:
        path = Path(name)
        if path.is_dir():
            # Hidden names are skipped, as a shell glob does: macOS writes ._
            # companion files next to tracks on non-Mac volumes.
            found = sorted(
                p
                for p in path.iterdir()
                if p.suffix.casefold() == ".thd" and not p.name.startswith(".") and p.is_file()
            )
            if not found:
                raise ConversionError(f"no .thd files in {path}")
            tracks.extend(found)
        elif path.is_file():
            tracks.append(path)
        else:
            raise ConversionError(f"{path}: no such file or folder")
    seen: dict[str, Path] = {}
    for track in tracks:
        key = track.stem.casefold()
        if key in seen:
            raise ConversionError(f"{seen[key]} and {track} share the output name {track.stem}")
        seen[key] = track
    return tracks


def default_output_dir(first_input: Path) -> Path:
    # Absolute without following symlinks: the output sits next to the
    # folder as the user named it.
    path = Path(os.path.abspath(first_input))
    folder = path if path.is_dir() else path.parent
    return folder.with_name(f"{folder.name} (binaural)")


def output_name(track: Path, fmt: str) -> str:
    return f"{track.stem}.{fmt}"


def switch_to_mount_owner(mount: Path) -> None:
    """As root, take the user and group that own `mount`, so that on a Linux
    host the output belongs to the owner of the shared folder."""
    if os.geteuid() != 0:
        return
    st = os.stat(mount)
    if st.st_uid == 0:
        return
    os.setgroups([])
    os.setgid(st.st_gid)
    os.setuid(st.st_uid)


def check_output_inside(out_dir: Path, mount: Path) -> None:
    """Refuse an output folder outside `mount`, symlinks resolved: the container
    removes it on exit."""
    if not Path(os.path.realpath(out_dir)).is_relative_to(os.path.realpath(mount)):
        raise ConversionError(
            f"{out_dir}: outside {mount}, the current folder as the container sees it; "
            "run atmos2binaural in the folder that holds the album folders"
        )


def check_deps() -> None:
    for path in (DEPS / "bin" / "orender", DEPS / "bin" / "harletty", DEPS / "lib" / PLUGIN):
        if not path.is_file():
            raise ConversionError(f"{path} not found; run scripts/build-deps.sh first")


def probe(track: Path) -> Probe:
    done = run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate", "-of", "json", str(track),
        ]
    )
    stream = (json.loads(done.stdout).get("streams") or [{}])[0]
    codec = stream.get("codec_name", "none")
    rate = int(stream.get("sample_rate", 0))
    # ffprobe names any file ending in .thd truehd; a rate of 0 means it
    # found no TrueHD frame in it.
    if codec != "truehd" or rate <= 0:
        raise ConversionError(f"{track}: no TrueHD stream (codec {codec}, sample rate {rate})")
    return Probe(codec, rate)


def harletty_command() -> list[str]:
    return [str(DEPS / "bin" / "harletty"), "--loglevel", "warn", "decode", "-"]


def check_stream(track: Path, progress: Optional[Callable[[float], None]] = None) -> list[str]:
    """The damage lines the decoder reports for `track`."""
    stderr = run(harletty_command(), track, progress).stderr
    return [line for line in stderr.splitlines() if DAMAGE_PATTERN.search(line)]


def orender_command(
    f32: Path,
    tmp: Path,
    hrtf: str = "synthetic",
    preset: str = "dolby",
    levels: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> list[str]:
    share, normalize, offsets = PRESETS[preset]
    zones = ",".join(f"{a + b:g}" for a, b in zip(offsets, levels))
    return [
        str(DEPS / "bin" / "orender"), "render", "-",
        "--config", str(tmp / "orender.yaml"),
        "--bridge-path", str(DEPS / "lib" / PLUGIN),
        "--output-mode", "binaural", "--hrir-source", hrtf,
        "--output-backend", "file", "--output-file", str(f32), "--output-file-format", "raw_f32",
        "--binaural-lfe-gain-db", str(LFE_GAIN_DB), "--binaural-lfe-lowpass-hz", str(LFE_LOWPASS_HZ),
        "--binaural-hrtf-share", f"{share:g}", "--binaural-zone-gains-db", zones,
        *(["--binaural-direction-normalize"] if normalize else []),
        "--loglevel", "warn",
    ]


def find_error_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if ERROR_LINE.match(line)]


def render(
    track: Path,
    f32: Path,
    tmp: Path,
    hrtf: str = "synthetic",
    preset: str = "dolby",
    levels: tuple[float, float, float] = (0.0, 0.0, 0.0),
    progress: Optional[Callable[[float], None]] = None,
) -> list[str]:
    """Render `track` to the float file `f32`; returns orender's ERROR lines."""
    return find_error_lines(run(orender_command(f32, tmp, hrtf, preset, levels), track, progress).stderr)


def ebur128(
    source: Path,
    input_format: list[str],
    filters: str = "ebur128=peak=true",
    progress: Optional[Callable[[float], None]] = None,
) -> Measurement:
    command = ["ffmpeg", "-hide_banner", "-nostats", *input_format, "-i", "pipe:0", "-af", filters, "-f", "null", "-"]
    stderr = run(command, source, progress).stderr
    summary = stderr[stderr.rfind("Summary:"):]
    loudness = re.search(r"^\s+I:\s+(-?[\d.]+) LUFS", summary, re.M)
    peak = re.search(r"^\s+Peak:\s+(-?[\d.]+|-inf) dBFS", summary, re.M)
    if "Summary:" not in stderr or not loudness or not peak:
        raise ConversionError(f"{source}: no loudness summary from ffmpeg ebur128:\n{stderr.strip()}")
    return Measurement(float(peak.group(1)), float(loudness.group(1)))


def measure(f32: Path, sample_rate: int, progress: Optional[Callable[[float], None]] = None) -> Measurement:
    return ebur128(f32, ["-f", "f32le", "-ar", str(sample_rate), "-ch_layout", "stereo"], progress=progress)


def playback_loudness(track: Path, progress: Optional[Callable[[float], None]] = None) -> float:
    """Integrated loudness of `track` as a stereo player plays it: FFmpeg's
    stereo downmix, which is also what mpv, and with it IINA, outputs."""
    filters = "aformat=channel_layouts=stereo,ebur128=peak=true"
    return ebur128(track, ["-f", "truehd"], filters, progress).loudness_lufs


def encode_command(sample_rate: int, gain_db: float, fmt: str, dest: Path) -> list[str]:
    codec = ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"] if fmt == "flac" else ["-c:a", "pcm_s24le"]
    # The limiter runs at four times the rate, so it holds true peaks, not only samples, to the ceiling.
    limit = 10 ** (CEILING_DBTP / 20)
    return [
        "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sample_rate), "-ch_layout", "stereo",
        "-i", "pipe:0",
        "-af", f"volume={gain_db:.4f}dB:precision=float,aresample={4 * sample_rate},"
        f"alimiter=limit={limit:.6f}:level=false:latency=true,aresample={sample_rate}",
        *codec,
        str(dest),
    ]


def encode(
    f32: Path,
    sample_rate: int,
    gain_db: float,
    fmt: str,
    dest: Path,
    progress: Optional[Callable[[float], None]] = None,
) -> None:
    run(encode_command(sample_rate, gain_db, fmt, dest), f32, progress)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = tmp = bar = None
    created = done = False
    mount = os.environ.get(MOUNT_ENV)
    try:
        if mount:
            switch_to_mount_owner(Path(mount))
        tracks = collect_inputs(args.inputs)
        check_deps()
        out_dir = Path(args.output) if args.output else default_output_dir(Path(args.inputs[0]))
        if mount:
            check_output_inside(out_dir, Path(mount))
        created = not out_dir.exists()
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / TMP_DIR_NAME
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()

        damaged = 0
        for n, track in enumerate(tracks, 1):
            bar = TrackBar(f"{n}/{len(tracks)} {track.stem}")
            rate = probe(track).sample_rate
            damage = check_stream(track, bar.stage("checking"))
            if damage and not args.allow_damaged:
                raise ConversionError(f"{track}: damaged stream: {damage[0]}")
            f32 = tmp / f"{n}.f32"
            started = time.monotonic()
            errors = render(
                track, f32, tmp, args.hrtf, args.preset, (args.centre, args.surround, args.height),
                bar.stage("rendering"),
            )
            seconds = time.monotonic() - started
            if errors and not args.allow_damaged:
                raise ConversionError(f"{track}: damaged stream: {errors[0]}")
            damage += errors
            loudness = measure(f32, rate, bar.stage("measuring")).loudness_lufs
            source = playback_loudness(track, bar.stage("measuring source"))
            gain = source - loudness
            dest = tmp / output_name(track, args.format)
            encode(f32, rate, gain, args.format, dest, bar.stage("encoding"))
            f32.unlink()
            os.replace(dest, out_dir / dest.name)
            line = (
                f"{n}/{len(tracks)} {track.stem}: {seconds:.1f} s, {source:.1f} LUFS, gain {gain:+.1f} dB"
            )
            if damage:
                damaged += 1
                line += f", damaged: {damage[0]}"
            bar.close(line)
            bar = None

        if damaged:
            print(f"damaged tracks: {damaged}")
        done = True
        return 0
    except (ConversionError, OSError) as e:
        if bar:
            bar.stop()
        print(f"atmos2binaural: {e}", file=sys.stderr)
        return 1
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        if created and not done:
            try:
                out_dir.rmdir()  # only while empty
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
