FROM rust:1.99.1-slim-trixie AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential pkg-config clang libclang-dev libpipewire-0.3-dev
WORKDIR /src
COPY atmos2binaural.py ./
COPY scripts/ scripts/
COPY patches/ patches/
RUN scripts/build-deps.sh

FROM debian:trixie-slim AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg python3 libpipewire-0.3-0t64 tini \
 && rm -rf /var/lib/apt/lists/*
COPY atmos2binaural.py /opt/atmos2binaural/
COPY --from=build /src/deps/ /opt/atmos2binaural/deps/

FROM runtime AS test
COPY tests/ /opt/atmos2binaural/tests/
WORKDIR /opt/atmos2binaural
RUN python3 -m unittest

FROM runtime
ENV ATMOS2BINAURAL_MOUNT=/data
WORKDIR /data
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/opt/atmos2binaural/atmos2binaural.py"]
