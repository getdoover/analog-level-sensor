# Rust build for the analog level sensor.
#
# The app depends on the sibling doover-rs checkout by path
# (../../doover-rs/doover), so the build CONTEXT must be the parent directory
# that holds both `analog-level-sensor/` and `doover-rs/`. Build from there:
#
#   cd ~/Coding/doover
#   docker build -f analog-level-sensor/rust/Dockerfile \
#     -t ghcr.io/getdoover/analog-level-sensor-rs:main .
#
# (For a self-contained CI build, pin doover-rs as a git dependency in
# rust/Cargo.toml instead — see the comment there — and build with just the
# rust/ dir as context.)

FROM rust:1-alpine AS builder
# musl-dev: static linking. protobuf-dev: doover-proto's build script needs
# protoc (on Alpine/musl the vendored protoc isn't used).
RUN apk add --no-cache musl-dev protobuf-dev
ENV PROTOC=/usr/bin/protoc
WORKDIR /build
# Copy the library (whole workspace, so its members resolve) and the app.
# A clean doover-rs checkout (no target/) keeps this fast.
COPY doover-rs/ ./doover-rs/
COPY analog-level-sensor/rust/ ./analog-level-sensor/rust/
WORKDIR /build/analog-level-sensor/rust
RUN cargo build --release

# Fully static musl binary -> runs on scratch (final image ~2 MB).
FROM scratch
COPY --from=builder /build/analog-level-sensor/rust/target/release/analog-level-sensor /analog-level-sensor
ENTRYPOINT ["/analog-level-sensor"]
