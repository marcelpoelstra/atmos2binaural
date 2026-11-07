# atmos2binaural

Converts Dolby TrueHD files (`.thd`) to binaural stereo for headphones, as FLAC or WAV. Works with Dolby Atmos as well as plain 5.1 and 7.1, for a single track or a whole album. Runs on macOS and Linux.

## Requirements

- Python 3.9 or newer
- FFmpeg 6.1 or newer
- For the one-time dependency build: git, a C compiler and [Rust](https://rustup.rs) 1.95 or newer

macOS (the Command Line Tools bring Python, git and the compiler):

```bash
xcode-select --install
brew install ffmpeg
```

Debian and Ubuntu:

```bash
sudo apt install ffmpeg python3 git build-essential pkg-config clang libclang-dev libpipewire-0.3-dev
```

## Install

```bash
git clone https://github.com/marcelpoelstra/atmos2binaural.git
cd atmos2binaural
scripts/build-deps.sh
```

`scripts/build-deps.sh` downloads and builds the renderer and the TrueHD decoder into `deps/`. It takes a few minutes and is needed once.

## Usage

Convert an album folder:

```bash
./atmos2binaural.py "/Music/My Atmos Album"
```

This writes `/Music/My Atmos Album (binaural)/` with one FLAC file per track, named after the source files.

| Option | Effect |
| --- | --- |
| `-o FOLDER` | Write to `FOLDER` instead; it is created when missing |
| `-f wav` | Write WAV instead of FLAC |
| `--allow-damaged` | Convert damaged tracks anyway, see below |

You can pass several files and folders; a folder contributes the `.thd` files directly inside it. For example:

```bash
./atmos2binaural.py -f wav -o ~/Converted track01.thd track02.thd
```

## Output

- 24-bit FLAC or WAV at the sample rate of the source.
- All tracks of one run get the same gain, which puts the loudest peak at -1 dBTP. Convert a whole album in one run to keep its tracks at the right level relative to each other.
- The LFE channel is included, in both ears.
- Existing files of the same name are overwritten. A run that fails writes no files.
- Tags and cover art are not copied.

## Damaged files

Every track is checked with the TrueHD decoder first. A damaged track stops the run:

```text
atmos2binaural: /Music/Album/01.thd: damaged stream: [...] Parse error at frame 20445: ...
```

With `--allow-damaged` it is converted anyway; it comes out shorter where the decoder dropped audio, and the run ends with `damaged tracks: <count>`.

## Troubleshooting

| Message | What to do |
| --- | --- |
| `.../deps/bin/orender not found; run scripts/build-deps.sh first` | Run `scripts/build-deps.sh` |
| `<file>: no TrueHD stream (...)` | The file holds no TrueHD audio; only raw TrueHD streams are read, not MKV or M2TS |
| `no .thd files in <folder>` | Point to the folder that holds the `.thd` files |
| `<file> and <file> share the output name <name>` | Two inputs would write the same output file; rename one of them |
| `... No such file or directory: 'ffprobe'` | Install FFmpeg |

## Licence

GPL-3.0-or-later, see `LICENSE`. The `harletty` decoder that `scripts/build-deps.sh` builds is LGPL-3.0.
