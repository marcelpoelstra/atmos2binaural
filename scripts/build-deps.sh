#!/usr/bin/env bash
# Builds the patched orender, the harletty dolby plugin and the harletty
# decoder into deps/ next to atmos2binaural.py.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$root/atmos2binaural.py" ]]; then
    echo "build-deps: $root does not hold atmos2binaural.py" >&2
    exit 1
fi
deps="$root/deps"

missing() {
    echo "build-deps: $1 not found; on Debian or Ubuntu install: build-essential pkg-config clang libclang-dev libpipewire-0.3-dev" >&2
    exit 1
}
for tool in git cargo cc; do
    command -v "$tool" >/dev/null || missing "$tool"
done
cargo_version="$(cargo --version | awk '{print $2}')"
major="${cargo_version%%.*}"
minor="${cargo_version#*.}"
minor="${minor%%.*}"
if ! (( major > 1 || (major == 1 && minor >= 95) )); then
    echo "build-deps: cargo $cargo_version is older than 1.95" >&2
    exit 1
fi
case "$(uname -s)" in
    Linux)
        command -v clang >/dev/null || missing clang
        command -v pkg-config >/dev/null || missing pkg-config
        pkg-config --exists libpipewire-0.3 || missing libpipewire-0.3
        # Where and under which names clang-sys 1.9.1 looks for libclang.
        libclang="$(find /usr/lib /usr/lib64 /usr/local/lib -maxdepth 3 \
            \( -name 'libclang.so*' -o -name 'libclang-[0-9]*.so*' \) -print -quit 2>/dev/null || true)"
        [[ -n "${LIBCLANG_PATH:-}$libclang" ]] || missing libclang
        plugin=libharletty_dolby_bridge.so
        ;;
    *)
        plugin=libharletty_dolby_bridge.dylib
        ;;
esac

rm -rf "$deps"
mkdir -p "$deps/build" "$deps/bin" "$deps/lib"

omniphony="$deps/build/Omniphony-v0.7.0"
git clone --quiet --depth 1 --branch v0.7.0 https://github.com/mgth/Omniphony.git "$omniphony"
rev="$(git -C "$omniphony" rev-parse HEAD)"
if [[ "$rev" != 353e135fa01e181bea36d83099a5951fd5c34180 ]]; then
    echo "build-deps: Omniphony v0.7.0 is $rev, expected 353e135fa01e181bea36d83099a5951fd5c34180" >&2
    exit 1
fi
git -C "$omniphony" apply "$root/patches/omniphony-v0.7.0-binaural-lfe.patch"
git -C "$omniphony" apply "$root/patches/omniphony-v0.7.0-binaural-balance.patch"
(cd "$omniphony/omniphony-renderer" && cargo build --release --locked --target-dir target)
cp "$omniphony/omniphony-renderer/target/release/orender" "$deps/bin/"

bridge="$deps/build/harletty-bridge"
git clone --quiet --depth 1 --branch v0.9.0 https://github.com/harletty/harletty-bridge.git "$bridge"
rev="$(git -C "$bridge" rev-parse HEAD)"
if [[ "$rev" != 9556bcedfd6e71461d49fd3f03fcfdf87351b67f ]]; then
    echo "build-deps: harletty-bridge v0.9.0 is $rev, expected 9556bcedfd6e71461d49fd3f03fcfdf87351b67f" >&2
    exit 1
fi

# The bridge builds against this Omniphony revision, as the sibling folder
# its Cargo.toml files name.
pinned="$deps/build/Omniphony"
git init --quiet "$pinned"
git -C "$pinned" remote add origin https://github.com/mgth/Omniphony.git
git -C "$pinned" fetch --quiet --depth 1 origin 55af2c57d6092f72478c4a8f36350bb71703185e
git -C "$pinned" checkout --quiet FETCH_HEAD

(cd "$bridge" && cargo build --release --locked --target-dir target -p harletty-dolby-bridge -p harletty)
cp "$bridge/target/release/$plugin" "$deps/lib/"
cp "$bridge/target/release/harletty" "$deps/bin/"

# Finder can write .DS_Store into a folder while it is being removed; a
# second pass removes what it wrote.
rm -rf "$deps/build" 2>/dev/null || rm -rf "$deps/build"
"$deps/bin/orender" --version
