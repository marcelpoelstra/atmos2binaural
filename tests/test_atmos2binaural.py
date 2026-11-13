import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import atmos2binaural as a2b
from tests.fixtures import damage, fake_deps, make_truehd

needs_deps = unittest.skipUnless(
    (a2b.DEPS / "bin" / "orender").exists(), "run scripts/build-deps.sh first"
)


def ffmpeg_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def stream_info(path: Path) -> dict:
    done = ffmpeg_tool(
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,bits_per_raw_sample,duration_ts",
        "-of", "json", str(path),
    )
    return json.loads(done.stdout)["streams"][0]


def levels(path: Path) -> tuple:
    """(integrated loudness in LUFS, true peak in dBTP) of an audio file."""
    err = ffmpeg_tool(
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"
    ).stderr
    summary = err[err.rindex("Summary:"):]
    loudness = float(re.search(r"^\s+I:\s+(-?[\d.]+) LUFS", summary, re.M).group(1))
    peak = float(re.search(r"^\s+Peak:\s+(-?[\d.]+|-inf) dBFS", summary, re.M).group(1))
    return loudness, peak


def sine_f32(path: Path) -> Path:
    """2 s of a 1 kHz stereo sine at amplitude 0.5 (-6 dBTP) as raw float."""
    ffmpeg_tool(
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=2",
        "-af", "volume=4,pan=stereo|FL=c0|FR=c0", "-f", "f32le", str(path),
    )
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()

    def touch(self, relative: str) -> Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path


class InputTests(TempDirTestCase):
    def test_collect_inputs_folder_in_name_order(self):
        for name in ("album/b.thd", "album/a.thd", "album/c.txt", "album/sub/d.thd"):
            self.touch(name)
        tracks = a2b.collect_inputs([str(self.tmp / "album")])
        self.assertEqual([t.name for t in tracks], ["a.thd", "b.thd"])

    def test_collect_inputs_mixes_files_and_folders(self):
        x = self.touch("x.thd")
        self.touch("folder/y.thd")
        tracks = a2b.collect_inputs([str(x), str(self.tmp / "folder")])
        self.assertEqual([t.name for t in tracks], ["x.thd", "y.thd"])

    def test_collect_inputs_rejects_duplicate_stems(self):
        one = self.touch("d1/x.thd")
        two = self.touch("d2/x.thd")
        with self.assertRaises(a2b.ConversionError) as caught:
            a2b.collect_inputs([str(one), str(two)])
        self.assertIn("x", str(caught.exception))

    def test_collect_inputs_rejects_stems_differing_in_case(self):
        one = self.touch("d1/x.thd")
        two = self.touch("d2/X.thd")
        with self.assertRaises(a2b.ConversionError):
            a2b.collect_inputs([str(one), str(two)])

    def test_collect_inputs_rejects_empty_folder(self):
        (self.tmp / "empty").mkdir()
        with self.assertRaises(a2b.ConversionError) as caught:
            a2b.collect_inputs([str(self.tmp / "empty")])
        self.assertTrue(str(caught.exception).startswith("no .thd files in"))

    def test_collect_inputs_rejects_missing_path(self):
        with self.assertRaises(a2b.ConversionError):
            a2b.collect_inputs([str(self.tmp / "nope.thd")])

    def test_collect_inputs_skips_hidden_files(self):
        self.touch("album/._a.thd")
        self.touch("album/a.thd")
        tracks = a2b.collect_inputs([str(self.tmp / "album")])
        self.assertEqual([t.name for t in tracks], ["a.thd"])

    def test_collect_inputs_accepts_upper_case_extension(self):
        self.touch("album/A.THD")
        self.touch("album/b.thd")
        tracks = a2b.collect_inputs([str(self.tmp / "album")])
        self.assertEqual([t.name for t in tracks], ["A.THD", "b.thd"])


class OutputFolderTests(TempDirTestCase):
    def test_default_output_dir_keeps_symlinked_folder(self):
        real = self.tmp / "real" / "Album"
        real.mkdir(parents=True)
        link = self.tmp / "Album"
        link.symlink_to(real)
        self.assertEqual(a2b.default_output_dir(link), self.tmp / "Album (binaural)")

    def test_default_output_dir_for_folder(self):
        album = self.tmp / "m" / "My Atmos Album"
        album.mkdir(parents=True)
        self.assertEqual(
            a2b.default_output_dir(album),
            self.tmp / "m" / "My Atmos Album (binaural)",
        )

    def test_default_output_dir_for_file(self):
        self.assertEqual(
            a2b.default_output_dir(Path("/m/Album/t.thd")),
            Path("/m/Album (binaural)"),
        )

    def test_default_output_dir_resolves_relative_input(self):
        album = self.tmp / "Album"
        album.mkdir()
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(album)
        self.assertEqual(a2b.default_output_dir(Path(".")), self.tmp / "Album (binaural)")

    def test_output_name(self):
        self.assertEqual(a2b.output_name(Path("x.thd"), "wav"), "x.wav")


class ArgumentTests(unittest.TestCase):
    def test_parse_args_defaults(self):
        args = a2b.parse_args(["a.thd"])
        self.assertEqual(args.format, "flac")
        self.assertIsNone(args.output)
        self.assertIs(args.allow_damaged, False)
        self.assertEqual(args.hrtf, "synthetic")
        self.assertEqual(args.preset, "dolby")
        self.assertEqual((args.centre, args.surround, args.height), (0.0, 0.0, 0.0))

    def test_parse_args_hrtf_kemar(self):
        self.assertEqual(a2b.parse_args(["--hrtf", "kemar", "a.thd"]).hrtf, "kemar")

    def test_parse_args_preset_and_levels(self):
        args = a2b.parse_args(
            ["--preset", "fold-down", "--centre", "2", "--surround", "-1.5", "--height", "-3", "a.thd"]
        )
        self.assertEqual(args.preset, "fold-down")
        self.assertEqual((args.centre, args.surround, args.height), (2.0, -1.5, -3.0))

    def test_parse_args_allow_damaged(self):
        args = a2b.parse_args(["--allow-damaged", "a.thd"])
        self.assertIs(args.allow_damaged, True)


class CommandTests(unittest.TestCase):
    def test_orender_command_matches_design(self):
        deps = Path("/d/deps")
        plugin = "libharletty_dolby_bridge.dylib" if sys.platform == "darwin" else "libharletty_dolby_bridge.so"
        with mock.patch.object(a2b, "DEPS", deps):
            command = a2b.orender_command(Path("/a/t.thd"), Path("/o/tmp/1.f32"), Path("/o/tmp"))
        self.assertEqual(
            command,
            [
                "/d/deps/bin/orender", "render", "/a/t.thd",
                "--config", "/o/tmp/orender.yaml",
                "--bridge-path", f"/d/deps/lib/{plugin}",
                "--output-mode", "binaural", "--hrir-source", "synthetic",
                "--output-backend", "file", "--output-file", "/o/tmp/1.f32", "--output-file-format", "raw_f32",
                "--binaural-lfe-gain-db", "5.5", "--binaural-lfe-lowpass-hz", "120",
                "--binaural-hrtf-share", "0.15", "--binaural-zone-gains-db", "0,0,0",
                "--loglevel", "warn",
            ],
        )

    def test_orender_command_takes_kemar(self):
        command = a2b.orender_command(Path("/a/t.thd"), Path("/o/tmp/1.f32"), Path("/o/tmp"), "kemar")
        self.assertEqual(command[command.index("--hrir-source") + 1], "kemar")

    def test_orender_command_fold_down_adds_the_levels(self):
        command = a2b.orender_command(
            Path("/a/t.thd"), Path("/o/tmp/1.f32"), Path("/o/tmp"), "synthetic", "fold-down", (2.0, -1.5, -3.0)
        )
        self.assertEqual(command[command.index("--binaural-hrtf-share") + 1], "1")
        self.assertIn("--binaural-direction-normalize", command)
        self.assertEqual(command[command.index("--binaural-zone-gains-db") + 1], "2,-4.5,-3")

    def test_orender_command_dolby_keeps_the_directions_as_they_are(self):
        command = a2b.orender_command(
            Path("/a/t.thd"), Path("/o/tmp/1.f32"), Path("/o/tmp"), "synthetic", "dolby", (1.0, 0.0, 0.0)
        )
        self.assertNotIn("--binaural-direction-normalize", command)
        self.assertEqual(command[command.index("--binaural-zone-gains-db") + 1], "1,0,0")

    def test_harletty_command_matches_design(self):
        with mock.patch.object(a2b, "DEPS", Path("/d/deps")):
            command = a2b.harletty_command(Path("/a/t.thd"))
        self.assertEqual(command, ["/d/deps/bin/harletty", "--loglevel", "warn", "decode", "/a/t.thd"])

    def test_find_error_lines(self):
        error = "[2026-11-07T17:24:06Z ERROR harletty-bridge::diag] Parse error at frame 20445"
        stderr = "\n".join(
            [
                "[2026-11-07T17:24:06Z WARN  orender] config file not found",
                error,
                "[E][16:31:46.794503] pw.conf      | [          conf.c: 1050 try_load_conf()] can't load config client.conf",
            ]
        )
        self.assertEqual(a2b.find_error_lines(stderr), [error])


class ProbeTests(TempDirTestCase):
    def test_probe_reads_truehd_fixture(self):
        track = make_truehd(self.tmp / "a.thd")
        self.assertEqual(a2b.probe(track), a2b.Probe("truehd", 48000))

    def test_probe_rejects_text_file(self):
        track = self.tmp / "x.thd"
        track.write_text("not audio\n" * 100)
        with self.assertRaises(a2b.ConversionError):
            a2b.probe(track)


class RenderTests(TempDirTestCase):
    @needs_deps
    def test_render_writes_float_file(self):
        track = make_truehd(self.tmp / "a.thd")
        f32 = self.tmp / "a.f32"
        a2b.render(track, f32, self.tmp)
        self.assertEqual(f32.stat().st_size, 768000)

    @needs_deps
    def test_render_handles_spaces_and_ampersand(self):
        track = make_truehd(self.tmp / "A B" / "Track & Mix 01.thd")
        f32 = self.tmp / "A B" / "Track & Mix 01.f32"
        a2b.render(track, f32, self.tmp)
        self.assertEqual(f32.stat().st_size, 768000)

    def test_render_returns_error_lines(self):
        line = "[2026-11-07T17:24:06Z ERROR harletty-bridge::diag] Parse error at frame 20445"
        deps = fake_deps(self.tmp, line)
        with mock.patch.object(a2b, "DEPS", deps):
            errors = a2b.render(self.tmp / "t.thd", self.tmp / "t.f32", self.tmp)
        self.assertEqual(errors, [line])


class StreamCheckTests(TempDirTestCase):
    @needs_deps
    def test_check_stream_passes_intact_fixture(self):
        self.assertEqual(a2b.check_stream(make_truehd(self.tmp / "a.thd")), [])

    @needs_deps
    def test_check_stream_reports_damage(self):
        track = make_truehd(self.tmp / "a.thd")
        damage(track)
        lines = a2b.check_stream(track)
        self.assertTrue(lines)
        self.assertRegex(lines[0], "Parity check failed|Parse error")


class LevelTests(TempDirTestCase):
    def test_playback_loudness_is_the_stereo_downmix(self):
        track = make_truehd(self.tmp / "a.thd")
        stereo = self.tmp / "stereo.wav"
        # Float, as mpv downmixes: an integer target makes FFmpeg scale the downmix down.
        ffmpeg_tool("ffmpeg", "-v", "error", "-i", str(track), "-ac", "2", "-c:a", "pcm_f32le", str(stereo))
        self.assertAlmostEqual(a2b.playback_loudness(track), levels(stereo)[0], delta=0.05)

    def test_encode_applies_the_gain_below_the_ceiling(self):
        dest = self.tmp / "quiet.flac"
        a2b.encode(sine_f32(self.tmp / "sine.f32"), 48000, -6.0, "flac", dest)
        self.assertAlmostEqual(levels(dest)[1], -12.0, delta=0.2)
        self.assertEqual(stream_info(dest)["duration_ts"], 96000)

    def test_encode_limits_true_peak(self):
        dest = self.tmp / "loud.flac"
        a2b.encode(sine_f32(self.tmp / "sine.f32"), 48000, 12.0, "flac", dest)
        self.assertLessEqual(levels(dest)[1], -1.0)
        self.assertEqual(stream_info(dest)["duration_ts"], 96000)


class EncodeCommandTests(unittest.TestCase):
    def test_encode_command_flac_24(self):
        command = a2b.encode_command(Path("/o/tmp/1.f32"), 48000, -10.3, "flac", Path("/o/tmp/t.flac"))
        self.assertEqual(
            command,
            [
                "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "48000", "-ch_layout", "stereo",
                "-i", "/o/tmp/1.f32",
                "-af", "volume=-10.3000dB:precision=float,aresample=192000,"
                "alimiter=limit=0.891251:level=false:latency=true,aresample=48000",
                "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24",
                "/o/tmp/t.flac",
            ],
        )

    def test_encode_command_wav_24(self):
        command = a2b.encode_command(Path("/o/tmp/1.f32"), 48000, -10.3, "wav", Path("/o/tmp/t.wav"))
        self.assertEqual(command[-3:], ["-c:a", "pcm_s24le", "/o/tmp/t.wav"])


class MeasureTests(TempDirTestCase):
    def test_measure_reads_true_peak(self):
        f32 = self.tmp / "sine.f32"
        ffmpeg_tool(
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=2",
            "-af", "volume=4,pan=stereo|FL=c0|FR=c0", "-f", "f32le", str(f32),
        )
        self.assertAlmostEqual(a2b.measure(f32, 48000).true_peak_dbtp, -6.0, delta=0.2)


class ConversionTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.src = self.tmp / "in"
        self.out = self.tmp / "out"

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = a2b.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def outputs(self, pattern="*.flac"):
        return sorted(p.name for p in self.out.glob(pattern))

    def assert_no_outputs(self):
        self.assertEqual(self.outputs("*.flac"), [])
        self.assertFalse((self.out / a2b.TMP_DIR_NAME).exists())

    @needs_deps
    def test_convert_album_flac(self):
        make_truehd(self.src / "a.thd")
        make_truehd(self.src / "b.thd")
        code, _, err = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.outputs(), ["a.flac", "b.flac"])
        for name in self.outputs():
            info = stream_info(self.out / name)
            self.assertEqual(
                (info["codec_name"], info["sample_rate"], info["bits_per_raw_sample"], info["duration_ts"]),
                ("flac", "48000", "24", 96000),
            )
        for name in self.outputs():
            loudness, peak = levels(self.out / name)
            self.assertAlmostEqual(loudness, a2b.playback_loudness(self.src / f"{name[:-5]}.thd"), delta=0.3)
            self.assertLessEqual(peak, -1.0)

    @needs_deps
    def test_convert_wav(self):
        make_truehd(self.src / "a.thd")
        code, _, err = self.run_main(self.src, "-o", self.out, "-f", "wav")
        self.assertEqual(code, 0, err)
        info = stream_info(self.out / "a.wav")
        self.assertEqual((info["codec_name"], info["duration_ts"]), ("pcm_s24le", 96000))

    @needs_deps
    def test_mixed_rates_keep_their_own_format(self):
        make_truehd(self.src / "a.thd", sample_rate=48000)
        make_truehd(self.src / "b.thd", sample_rate=96000)
        code, _, err = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertEqual(stream_info(self.out / "a.flac")["sample_rate"], "48000")
        self.assertEqual(stream_info(self.out / "b.flac")["sample_rate"], "96000")

    @needs_deps
    def test_output_into_source_folder_keeps_sources(self):
        sources = [make_truehd(self.src / "a.thd"), make_truehd(self.src / "b.thd")]
        before = [sha256(s) for s in sources]
        code, _, err = self.run_main(self.src, "-o", self.src)
        self.assertEqual(code, 0, err)
        self.assertEqual([sha256(s) for s in sources], before)
        self.assertTrue((self.src / "a.flac").exists() and (self.src / "b.flac").exists())

    @needs_deps
    def test_failed_render_leaves_no_outputs(self):
        make_truehd(self.src / "a.thd")
        make_truehd(self.src / "b.thd")
        real = a2b.render
        calls = []

        def render(track, *args):
            calls.append(track)
            if len(calls) == 2:
                raise a2b.ConversionError("render failed")
            return real(track, *args)

        with mock.patch.object(a2b, "render", render):
            code, _, _ = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assert_no_outputs()

    @needs_deps
    def test_failed_encode_leaves_no_outputs(self):
        make_truehd(self.src / "a.thd")
        make_truehd(self.src / "b.thd")
        real = a2b.encode
        calls = []

        def encode(*args):
            calls.append(args)
            if len(calls) == 2:
                raise a2b.ConversionError("encode failed")
            return real(*args)

        with mock.patch.object(a2b, "encode", encode):
            code, _, _ = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assert_no_outputs()

    @needs_deps
    def test_stale_tmp_folder_is_replaced(self):
        make_truehd(self.src / "a.thd")
        stale = self.out / a2b.TMP_DIR_NAME
        stale.mkdir(parents=True)
        (stale / "old.f32").write_bytes(bytes(8))
        code, _, err = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 0, err)
        self.assertFalse(stale.exists())

    @needs_deps
    def test_not_truehd_input_fails_cleanly(self):
        make_truehd(self.src / "a.thd")
        (self.src / "x.thd").write_text("not audio\n" * 100)
        code, _, _ = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assert_no_outputs()

    @needs_deps
    def test_damaged_input_stops_the_run(self):
        make_truehd(self.src / "a.thd")
        damage(make_truehd(self.src / "b.thd"))
        code, _, err = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assert_no_outputs()
        self.assertIn("b.thd", err)

    ERROR_LINE = "[2026-11-07T17:24:06Z ERROR harletty-bridge::diag] Parse error at frame 20445"

    def render_with_error_on(self, name):
        real = a2b.render

        def render(track, *args):
            return real(track, *args) + ([self.ERROR_LINE] if track.name == name else [])

        return render

    @needs_deps
    def test_render_error_line_stops_the_run(self):
        make_truehd(self.src / "a.thd")
        make_truehd(self.src / "b.thd")
        with mock.patch.object(a2b, "render", self.render_with_error_on("b.thd")):
            code, _, err = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assert_no_outputs()
        self.assertIn("b.thd", err)
        self.assertIn(self.ERROR_LINE, err)

    @needs_deps
    def test_allow_damaged_reports_render_error_line(self):
        make_truehd(self.src / "a.thd")
        make_truehd(self.src / "b.thd")
        with mock.patch.object(a2b, "render", self.render_with_error_on("b.thd")):
            code, out, err = self.run_main(self.src, "-o", self.out, "--allow-damaged")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.outputs(), ["a.flac", "b.flac"])
        self.assertIn(f"damaged: {self.ERROR_LINE}", out)
        self.assertIn("damaged tracks: 1", out)

    @needs_deps
    def test_failed_move_leaves_no_outputs(self):
        make_truehd(self.src / "a.thd")
        make_truehd(self.src / "b.thd")
        real = os.replace
        calls = []

        def replace(src, dst):
            calls.append(src)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(src, dst)

        with mock.patch.object(a2b.os, "replace", replace):
            code, _, err = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assert_no_outputs()
        self.assertIn("disk full", err)

    @needs_deps
    def test_output_path_that_is_a_file_fails_cleanly(self):
        make_truehd(self.src / "a.thd")
        blocker = self.tmp / "out"
        blocker.write_text("not a folder")
        code, _, err = self.run_main(self.src, "-o", blocker)
        self.assertEqual(code, 1)
        self.assertIn(str(blocker), err)
        self.assertEqual(blocker.read_text(), "not a folder")

    @needs_deps
    def test_failed_run_removes_the_output_folder_it_created(self):
        self.src.mkdir(parents=True)
        (self.src / "x.thd").write_text("not audio\n" * 100)
        code, _, _ = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assertFalse(self.out.exists())

    @needs_deps
    def test_failed_run_keeps_an_existing_output_folder(self):
        self.src.mkdir(parents=True)
        (self.src / "x.thd").write_text("not audio\n" * 100)
        self.out.mkdir()
        code, _, _ = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assertTrue(self.out.is_dir())

    def test_missing_deps_names_the_build_script(self):
        self.touch("in/a.thd")
        with mock.patch.object(a2b, "DEPS", self.tmp / "no-deps"):
            code, _, err = self.run_main(self.src, "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("scripts/build-deps.sh", err)
        self.assertFalse(self.out.exists())

    @needs_deps
    def test_allow_damaged_converts_damaged_input(self):
        make_truehd(self.src / "a.thd")
        damage(make_truehd(self.src / "b.thd"))
        code, out, err = self.run_main(self.src, "-o", self.out, "--allow-damaged")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.outputs(), ["a.flac", "b.flac"])
        self.assertIn("damaged:", out)
        self.assertIn("damaged tracks: 1", out)


class ContainerModeTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.mount = self.tmp / "data"
        self.mount.mkdir()

    def switch(self, euid, uid, gid):
        calls = mock.Mock()
        stat = os.stat_result((0o40755, 0, 0, 0, uid, gid, 0, 0, 0, 0))
        with mock.patch.object(a2b.os, "geteuid", return_value=euid), \
             mock.patch.object(a2b.os, "stat", return_value=stat), \
             mock.patch.object(a2b.os, "setgroups", calls.setgroups), \
             mock.patch.object(a2b.os, "setgid", calls.setgid), \
             mock.patch.object(a2b.os, "setuid", calls.setuid):
            a2b.switch_to_mount_owner(self.mount)
        return calls.mock_calls

    def test_switch_takes_owner_and_group_as_root(self):
        self.assertEqual(self.switch(0, 1000, 1001), [mock.call.setgroups([]), mock.call.setgid(1001), mock.call.setuid(1000)])

    def test_switch_keeps_root_for_a_root_owned_mount(self):
        self.assertEqual(self.switch(0, 0, 0), [])

    def test_switch_does_nothing_without_root(self):
        self.assertEqual(self.switch(1000, 1000, 1001), [])

    def test_guard_accepts_folders_in_the_mount(self):
        a2b.check_output_inside(self.mount / "album (binaural)", self.mount)
        a2b.check_output_inside(self.mount, self.mount)

    def test_guard_refuses_default_output_of_the_mount(self):
        out = a2b.default_output_dir(self.mount)
        with self.assertRaises(a2b.ConversionError) as caught:
            a2b.check_output_inside(out, self.mount)
        self.assertEqual(
            str(caught.exception),
            f"{out}: outside {self.mount}, the current folder as the container sees it; "
            "run atmos2binaural in the folder that holds the album folders",
        )

    def test_guard_refuses_a_host_path(self):
        with self.assertRaises(a2b.ConversionError):
            a2b.check_output_inside(self.tmp / "Converted", self.mount)

    def test_guard_refuses_a_symlink_out_of_the_mount(self):
        (self.tmp / "elsewhere").mkdir()
        (self.mount / "link").symlink_to(self.tmp / "elsewhere")
        with self.assertRaises(a2b.ConversionError):
            a2b.check_output_inside(self.mount / "link" / "out", self.mount)

    def test_main_refuses_output_outside_the_mount(self):
        for name in ("deps/bin/orender", "deps/bin/harletty", f"deps/lib/{a2b.PLUGIN}", "data/album/a.thd"):
            self.touch(name)
        outside = self.tmp / "Converted"
        err = io.StringIO()
        with mock.patch.dict(os.environ, {a2b.MOUNT_ENV: str(self.mount)}), \
             mock.patch.object(a2b, "DEPS", self.tmp / "deps"), \
             mock.patch.object(a2b, "switch_to_mount_owner") as switch, \
             contextlib.redirect_stderr(err):
            code = a2b.main([str(self.mount / "album"), "-o", str(outside)])
        self.assertEqual(code, 1)
        switch.assert_called_once_with(self.mount)
        self.assertIn(f"{outside}: outside {self.mount}", err.getvalue())
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
