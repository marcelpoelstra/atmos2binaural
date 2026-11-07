"""Test inputs: TrueHD files made with FFmpeg, damaged copies and a fake deps/ tree."""

import subprocess
from pathlib import Path


def make_truehd(path: Path, sample_rate: int = 48000, seconds: float = 2.0) -> Path:
    """A plain 5.1 TrueHD file of a 440 Hz sine on every channel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate={sample_rate}:duration={seconds}",
            "-af", "pan=5.1(side)|FL=c0|FR=c0|FC=c0|LFE=c0|SL=c0|SR=c0",
            "-sample_fmt", "s32p", "-c:a", "truehd", "-strict", "experimental",
            "-f", "truehd", str(path),
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return path


def damage(path: Path) -> None:
    """Overwrite 4096 bytes at the middle of the file with zeros."""
    with path.open("r+b") as f:
        f.seek(path.stat().st_size // 2)
        f.write(bytes(4096))


def fake_deps(root: Path, stderr_line: str) -> Path:
    """A deps/ tree whose bin/orender prints `stderr_line` to its error
    output, writes one second of silence as the float file and exits 0."""
    deps = root / "deps"
    (deps / "bin").mkdir(parents=True)
    (deps / "lib").mkdir()
    orender = deps / "bin" / "orender"
    orender.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        f"sys.stderr.write({stderr_line!r} + '\\n')\n"
        "with open(args[args.index('--output-file') + 1], 'wb') as f:\n"
        "    f.write(bytes(8 * 48000))\n"
    )
    orender.chmod(0o755)
    return deps
