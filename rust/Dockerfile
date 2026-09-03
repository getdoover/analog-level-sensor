# Rust build for the analog level sensor. Build from the repo root:
#
#   docker build -f rust/Dockerfile -t ghcr.io/getdoover/analog-level-sensor-rs:main .
#
# doover-rs is pulled from git (pinned in Cargo.toml), so no special build
# context is needed.

FROM rust:1-alpine AS builder
# git: fetch the doover-rs git dependency. protobuf-dev: doover-proto's build
# script needs protoc (on Alpine/musl the vendored protoc isn't used).
RUN apk add --no-cache musl-dev protobuf-dev git
ENV PROTOC=/usr/bin/protoc
WORKDIR /build
COPY rust/ ./
RUN cargo build --release

# Fully static musl binary -> runs on scratch (final image ~2 MB).
FROM scratch
COPY --from=builder /build/target/release/analog-level-sensor /analog-level-sensor
ENTRYPOINT ["/analog-level-sensor"]
