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
import time
from pathlib import Path
from typing import NamedTuple, Optional

LFE_GAIN_DB = 5.5
LFE_LOWPASS_HZ = 120
CEILING_DBTP = -1.0
TMP_DIR_NAME = ".atmos2binaural-tmp"
DEPS = Path(__file__).resolve().parent / "deps"
PLUGIN = "libharletty_dolby_bridge.dylib" if sys.platform == "darwin" else "libharletty_dolby_bridge.so"
DAMAGE_PATTERN = re.compile(r"Parity check failed|could not be decoded|Parse error|ERROR")
ERROR_LINE = re.compile(r"^\[\S+ ERROR\b")


class ConversionError(Exception):
    """A step failed; the message says which and why."""


class Probe(NamedTuple):
    codec: str
    sample_rate: int


class Measurement(NamedTuple):
    true_peak_dbtp: float  # float("-inf") for silence
    loudness_lufs: float


def run(command: list[str]) -> subprocess.CompletedProcess:
    """Run `command` without a shell; a non-zero exit raises ConversionError
    with the command and its error output."""
    try:
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as e:
        raise ConversionError(f"{shlex.join(command)}: {e}") from e
    if done.returncode != 0:
        raise ConversionError(
            f"{shlex.join(command)} exited with status {done.returncode}:\n{done.stderr.strip()}"
        )
    return done


def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="atmos2binaural",
        description="Convert Dolby TrueHD files to binaural stereo FLAC or WAV.",
    )
    parser.add_argument("inputs", nargs="+", metavar="INPUT", help=".thd file or folder of .thd files")
    parser.add_argument("-o", "--output", metavar="OUTPUT_DIR", help="output folder")
    parser.add_argument("-f", "--format", choices=("flac", "wav"), default="flac", help="output format (default flac)")
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


def harletty_command(track: Path) -> list[str]:
    return [str(DEPS / "bin" / "harletty"), "--loglevel", "warn", "decode", str(track)]


def check_stream(track: Path) -> list[str]:
    """The damage lines the decoder reports for `track`."""
    stderr = run(harletty_command(track)).stderr
    return [line for line in stderr.splitlines() if DAMAGE_PATTERN.search(line)]


def orender_command(track: Path, f32: Path, tmp: Path) -> list[str]:
    return [
        str(DEPS / "bin" / "orender"), "render", str(track),
        "--config", str(tmp / "orender.yaml"),
        "--bridge-path", str(DEPS / "lib" / PLUGIN),
        "--output-mode", "binaural",
        "--output-backend", "file", "--output-file", str(f32), "--output-file-format", "raw_f32",
        "--binaural-lfe-gain-db", str(LFE_GAIN_DB), "--binaural-lfe-lowpass-hz", str(LFE_LOWPASS_HZ),
        "--loglevel", "warn",
    ]


def find_error_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if ERROR_LINE.match(line)]


def render(track: Path, f32: Path, tmp: Path) -> list[str]:
    """Render `track` to the float file `f32`; returns orender's ERROR lines."""
    return find_error_lines(run(orender_command(track, f32, tmp)).stderr)


def measure(f32: Path, sample_rate: int) -> Measurement:
    stderr = run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-f", "f32le", "-ar", str(sample_rate), "-ch_layout", "stereo", "-i", str(f32),
            "-af", "ebur128=peak=true", "-f", "null", "-",
        ]
    ).stderr
    summary = stderr[stderr.rfind("Summary:"):]
    loudness = re.search(r"^\s+I:\s+(-?[\d.]+) LUFS", summary, re.M)
    peak = re.search(r"^\s+Peak:\s+(-?[\d.]+|-inf) dBFS", summary, re.M)
    if "Summary:" not in stderr or not loudness or not peak:
        raise ConversionError(f"{f32}: no loudness summary from ffmpeg ebur128:\n{stderr.strip()}")
    return Measurement(float(peak.group(1)), float(loudness.group(1)))


def album_gain(peaks: list[float], ceiling: float = CEILING_DBTP) -> float:
    """The gain that brings the highest true peak to `ceiling`; silent tracks
    (-inf) do not count."""
    audible = [p for p in peaks if p != float("-inf")]
    return ceiling - max(audible) if audible else 0.0


def encode_command(f32: Path, sample_rate: int, gain_db: float, fmt: str, dest: Path) -> list[str]:
    codec = ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"] if fmt == "flac" else ["-c:a", "pcm_s24le"]
    return [
        "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sample_rate), "-ch_layout", "stereo",
        "-i", str(f32), "-af", f"volume={gain_db:.4f}dB:precision=float",
        *codec,
        str(dest),
    ]


def encode(f32: Path, sample_rate: int, gain_db: float, fmt: str, dest: Path) -> None:
    run(encode_command(f32, sample_rate, gain_db, fmt, dest))


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = tmp = None
    created = done = False
    try:
        tracks = collect_inputs(args.inputs)
        check_deps()
        out_dir = Path(args.output) if args.output else default_output_dir(Path(args.inputs[0]))
        created = not out_dir.exists()
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / TMP_DIR_NAME
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()

        rendered = []
        damaged = 0
        for n, track in enumerate(tracks, 1):
            rate = probe(track).sample_rate
            damage = check_stream(track)
            if damage and not args.allow_damaged:
                raise ConversionError(f"{track}: damaged stream: {damage[0]}")
            f32 = tmp / f"{n}.f32"
            started = time.monotonic()
            errors = render(track, f32, tmp)
            seconds = time.monotonic() - started
            if errors and not args.allow_damaged:
                raise ConversionError(f"{track}: damaged stream: {errors[0]}")
            damage += errors
            levels = measure(f32, rate)
            line = (
                f"{n}/{len(tracks)} {track.stem}: {seconds:.1f} s, "
                f"{levels.true_peak_dbtp:.1f} dBTP, {levels.loudness_lufs:.1f} LUFS"
            )
            if damage:
                damaged += 1
                line += f", damaged: {damage[0]}"
            print(line, flush=True)
            rendered.append((track, f32, rate, levels))

        gain = album_gain([levels.true_peak_dbtp for *_, levels in rendered])
        print(f"album gain {gain:.2f} dB")
        if damaged:
            print(f"damaged tracks: {damaged}")
        encoded = []
        for track, f32, rate, _ in rendered:
            dest = tmp / output_name(track, args.format)
            encode(f32, rate, gain, args.format, dest)
            f32.unlink()
            encoded.append(dest)
        moved = []
        try:
            for dest in encoded:
                os.replace(dest, out_dir / dest.name)
                moved.append(out_dir / dest.name)
        except OSError:
            for path in moved:
                path.unlink()
            raise
        done = True
        return 0
    except (ConversionError, OSError) as e:
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
