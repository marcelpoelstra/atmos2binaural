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

With Docker you need only Docker. macOS:

```bash
brew install colima docker docker-buildx
colima start
```

Then add the `cliPluginsExtraDirs` entry that `brew info docker-buildx` shows to `~/.docker/config.json`, so that Docker finds buildx. By default colima shares only your home folder with Docker. For music in other folders, see [colima's FAQ](https://github.com/abiosoft/colima/blob/main/docs/FAQ.md#issue-with-docker-bind-mount-showing-empty).

Debian and Ubuntu:

```bash
sudo apt install docker.io docker-buildx
```

To run Docker without `sudo` on Linux, follow Docker's [Linux post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/).

## Install

```bash
git clone https://github.com/marcelpoelstra/atmos2binaural.git
cd atmos2binaural
scripts/build-deps.sh
```

`scripts/build-deps.sh` downloads and builds the renderer and the TrueHD decoder into `deps/`. It takes a few minutes and is needed once.

### Install with Docker

Add this function to `~/.zshrc` or `~/.bashrc`, then open a new terminal:

```bash
atmos2binaural() {
  BASE_IMAGE=ghcr.io/marcelpoelstra/atmos2binaural
  case $1 in
    1.[0-9]*.[0-9]* ) IMAGE=${BASE_IMAGE}:$1; shift ;;
    * ) IMAGE=${BASE_IMAGE}:latest; docker pull $IMAGE ;;
  esac
  docker run --rm -it -v "${PWD}":/data --workdir /data $IMAGE "$@"
}
```

The function runs the converter from the image `ghcr.io/marcelpoelstra/atmos2binaural`. Without a version as the first argument, it first pulls the latest image.

To build the image yourself:

```bash
git clone https://github.com/marcelpoelstra/atmos2binaural.git
cd atmos2binaural
git checkout v1.0.0
docker build -t ghcr.io/marcelpoelstra/atmos2binaural:1.0.0 .
```

Then pass `1.0.0` as the first argument, as the Usage section shows.

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
| `--hrtf kemar` | Use the measured KEMAR head instead of the default synthetic head: more front, back and height cues, but less bass and a brighter sound |
| `--allow-damaged` | Convert damaged tracks anyway, see below |

You can pass several files and folders; a folder contributes the `.thd` files directly inside it. For example:

```bash
./atmos2binaural.py -f wav -o ~/Converted track01.thd track02.thd
```

### Usage with Docker

With Docker the command is `atmos2binaural`, without `./` and `.py`. It can only see the folder you run it in. So go to the folder that holds your album folder, and give the album folder by its name:

```bash
cd ~/Music
atmos2binaural "My Atmos Album"
```

This writes `~/Music/My Atmos Album (binaural)/`.

- Names only: a path that starts with `/` or `~` does not work, also not after `-o`. Write `-o Converted` to get the output in `~/Music/Converted/`.
- With colima on macOS, the folder must be one that colima shares with Docker; by default that is only your home folder.
- The options are the same as above.

Each run first fetches the latest version. To run a specific version instead, put its number first:

```bash
atmos2binaural 1.0.0 "My Atmos Album"
```

## Output

- 24-bit FLAC or WAV at the sample rate of the source.
- Each track plays as loud as its TrueHD file does in a stereo player; a limiter keeps peaks at or below -1 dBTP.
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
| `<path>: no such file or folder` | Check the path. With Docker, give the path relative to the current folder |
| `<folder>: outside /data, the current folder as the container sees it; run atmos2binaural in the folder that holds the album folders` | With Docker: run the command in the folder that holds the album folders, and keep `-o` relative to the current folder |
| `failed to connect to the docker API at ...; check if the path is correct and if the daemon is running` | Start Docker: on macOS with `colima start`, on Linux by starting the Docker service |
| `cannot attach stdin to a TTY-enabled container because stdin is not a terminal` | With Docker: run the command in a terminal; its input cannot come from a pipe or a file |

## Licence

GPL-3.0-or-later, see `LICENSE`. The `harletty` decoder that `scripts/build-deps.sh` builds is LGPL-3.0. The Docker image is built from this repository: the `Dockerfile` and `scripts/build-deps.sh` name every upstream source revision it contains.
