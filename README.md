# atmos2binaural

atmos2binaural grew out of a personal frustration. Many music Blu-ray discs carry surround and Dolby Atmos mixes. Anyone who owns such discs shares that frustration: you cannot play those surround versions on the go.

You first need to rip the raw TrueHD files (.thd) from the original Blu-ray (instructions not provided for obvious reasons, Google is your friend) 
atmos2binaural then converts and downmixes them to 2-channel binaural audio, which keeps much of the surround sound. 
You can listen to the result on headphones from a portable music player or a smartphone. 
It even sounds great on plain stereo speakers, but do not expect a spectacular difference from the familiar stereo versions of the music in that case.

atmos2binaural reads Dolby TrueHD files (`.thd`) with Dolby Atmos, 7.1 or 5.1 audio, and writes FLAC or WAV files. 
It converts a single track or a whole album, and runs on macOS and Linux.

## Installation

You can run atmos2binaural with Docker, which is recommended for most users, or build it from source.

### With Docker (recommended)

On macOS, you can use Docker Desktop, or alternatively install Docker with colima:

```bash
brew install colima docker docker-buildx
colima start
```

Then add the `cliPluginsExtraDirs` entry that `brew info docker-buildx` shows to `~/.docker/config.json`, so that Docker finds buildx. colima shares only your home folder with Docker. For music in other folders, see [colima's FAQ](https://github.com/abiosoft/colima/blob/main/docs/FAQ.md#issue-with-docker-bind-mount-showing-empty).

On Debian and Ubuntu:

```bash
sudo apt install docker.io docker-buildx
```

To run Docker without `sudo` on Linux, follow Docker's [Linux post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/).

Next, add this function to `~/.zshrc` or `~/.bashrc`, and open a new terminal:

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

The `atmos2binaural` command now runs the converter from the image `ghcr.io/marcelpoelstra/atmos2binaural`. Without a version number as the first argument, each run first pulls the latest image.

To build the image yourself, replace `<version>` with a release number such as `1.0.0`:

```bash
git clone https://github.com/marcelpoelstra/atmos2binaural.git
cd atmos2binaural
git checkout v<version>
docker build -t ghcr.io/marcelpoelstra/atmos2binaural:<version> .
```

Then give that version number as the first argument when you run the command, as shown further below under Usage with Docker.

### From source

You need Python 3.9 or newer and FFmpeg 6.1 or newer. 
The one-time build of the renderer and the decoder also needs git, a C compiler and Rust 1.95 or newer.

On macOS, the Command Line Tools provide Python, git and the compiler. 
Homebrew provides FFmpeg and Rust:

```bash
xcode-select --install
brew install ffmpeg rust
```

On Debian and Ubuntu, install the packages, and then Rust with its installer, rustup:

```bash
sudo apt install ffmpeg python3 git curl build-essential pkg-config clang libclang-dev libpipewire-0.3-dev
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Open a new terminal after the Rust installation, so that the shell finds Rust.

Then download atmos2binaural and build its dependencies:

```bash
git clone https://github.com/marcelpoelstra/atmos2binaural.git
cd atmos2binaural
scripts/build-deps.sh
```

`scripts/build-deps.sh` downloads and builds the renderer and the TrueHD decoder into `deps/`. 
This takes a few minutes. Run it again after every new version update of atmos2binaural.

## Usage

In the `atmos2binaural` folder, convert an album by giving its folder:

```bash
./atmos2binaural.py "/Music/My Atmos Album"
```

This creates the folder `/Music/My Atmos Album (binaural)/` with one FLAC or WAV file per track, named after the original files.

You can also give single `.thd` files, or several files and folders at once. 
From a folder, atmos2binaural takes the `.thd` files directly inside it, it does not search subfolders. 
Without `-o`, the output goes to a folder next to the album folder, named `<album folder> (binaural)`. 
For single files, the album folder is the folder that holds the first file.

### Usage with Docker

With Docker, the command is `atmos2binaural`, without `./` and `.py`. 
The container sees only the current folder and the folders inside it. 
Change to the folder that holds your album folders, and give paths relative to it:

```bash
cd ~/Music
atmos2binaural "My Atmos Album"
```

This creates `~/Music/My Atmos Album (binaural)/`. 
The options below work the same way. A folder given with `-o` must also lie inside the current folder.

To run a specific version of the image, give its number first:

```bash
atmos2binaural 1.0.0 "My Atmos Album"
```

## Options

You can combine any of these options. Leave an option out to keep its default.

| Option | Possible values | Default | Effect |
| --- | --- | --- | --- |
| `-o FOLDER` | a folder | `<album folder> (binaural)`, next to the album folder | Writes the output files to `FOLDER`, and creates the folder if needed |
| `-f FORMAT` | `flac` or `wav` | `flac` | Sets the format of the output files |
| `--preset NAME` | `dolby` or `fold-down` | `dolby` | Sets the balance between the channels (see Presets) |
| `--centre DB` | a number of decibels, such as `3` or `-2` | `0` | Makes the centre louder with a positive number, or softer with a negative one. The centre is the centre channel and anything straight in front of you, such as a lead vocal |
| `--surround DB` | as for `--centre` | `0` | Does the same for the side and rear channels: anything beside or behind you |
| `--height DB` | as for `--centre` | `0` | Does the same for the height channels: anything above you |
| `--hrtf NAME` | `synthetic` or `kemar` | `synthetic` | Selects the head model that places the sound around your head (see Head models) |
| `--allow-damaged` | no value | off | Converts damaged tracks anyway (see Damaged files) |

### Presets

| Preset | Sound |
| --- | --- |
| `dolby` | Every channel keeps its own level. The sound is mostly plain stereo panning, with the head model contributing 15% |
| `fold-down` | Balanced like a stereo downmix of the surround mix: the surround channels play 3 dB softer than the front. The head model shapes the whole sound |

### Levels

`--centre`, `--surround` and `--height` add to the levels of the chosen preset. 
`--preset fold-down` on its own plays the surround channels 3 dB softer than the front. 
With `--preset fold-down --surround 2`, they play 1 dB softer. 
Each total level, the preset's part included, is capped at 24 dB up or down.

### Head models

`synthetic` colours the sound the least. 
`kemar` is based on measurements of a dummy head. 
It gives more front, back and height cues, but less bass and a brighter sound. 
The choice matters most with the `fold-down` preset, because `dolby` uses the head model for only 15% of the sound.

### Examples

```bash
# Make the centre 3 dB louder, e.g. for a centred lead vocal that sounds too far away
./atmos2binaural.py --centre 3 "/Music/My Atmos Album"

# Stereo downmix balance with even softer surrounds, as WAV files in ~/Converted
./atmos2binaural.py --preset fold-down --surround -2 -f wav -o ~/Converted "/Music/My Atmos Album"

# Two single tracks with the kemar head model
./atmos2binaural.py --hrtf kemar "/Music/My Atmos Album/01.thd" "/Music/My Atmos Album/02.thd"
```

## Output

atmos2binaural writes 24-bit FLAC or WAV files at the sample rate of the source. 
Each track plays as loud as its TrueHD file does in a stereo player such as IINA. 
A limiter keeps the peaks at or below -1 dBTP. The LFE (subwoofer) channel is included, in both ears.

Existing files with the same name are overwritten. A run that fails writes no files. Tags and cover art are not copied.

## Damaged files

Before converting, atmos2binaural checks every track with the TrueHD decoder. 
A damaged track stops the run with a message such as:

```text
atmos2binaural: /Music/My Atmos Album/01.thd: damaged stream: [...] Parse error at frame 20445: ...
```

With `--allow-damaged`, damaged tracks are converted anyway. 
They come out shorter where the decoder dropped audio, and the run ends with `damaged tracks: <count>`.

## Troubleshooting

| Message | Solution |
| --- | --- |
| `build-deps: <tool> not found; on Debian or Ubuntu install: ...` | Install what the message names, as shown under From source. For `cargo`, install Rust |
| `build-deps: cargo <version> is older than 1.95` | Update Rust with `rustup update`, or on macOS with `brew upgrade rust` |
| `.../deps/bin/orender not found; run scripts/build-deps.sh first` | Run `scripts/build-deps.sh` |
| `<file>: no TrueHD stream (...)` | The file holds no TrueHD audio that atmos2binaural can read. Give a raw TrueHD file (`.thd`); MKV and M2TS files are not supported |
| `no .thd files in <folder>` | Give the folder that holds the `.thd` files |
| `<file> and <file> share the output name <name>` | Two inputs would write the same output file. Rename one of them |
| `... No such file or directory: 'ffprobe'` | Install FFmpeg |
| `<path>: no such file or folder` | Check the path. With Docker, give the path relative to the current folder |
| `<folder>: outside /data, the current folder as the container sees it; run atmos2binaural in the folder that holds the album folders` | With Docker, run the command in the folder that holds the album folders, and keep `-o` inside that folder |
| `failed to connect to the docker API at ...; check if the path is correct and if the daemon is running` | Start Docker: on macOS with `colima start`, on Linux by starting the Docker service |
| `cannot attach stdin to a TTY-enabled container because stdin is not a terminal` | With Docker, run the command in a terminal. Its input cannot come from a pipe or a file |

## Licence

atmos2binaural is licensed under GPL-3.0-or-later; see `LICENSE`. 
The `harletty` decoder that `scripts/build-deps.sh` builds is licensed under LGPL-3.0.
