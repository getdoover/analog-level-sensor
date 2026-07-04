# Rust build for the analog level sensor.
#
# NOTE ON BUILD CONTEXT: this app depends on the `doover-rs` library via a
# sibling path dependency (../../doover-rs), so the Docker build context must be
# the PARENT directory that contains both `analog-level-sensor/` and
# `doover-rs/`. Build it from that parent:
#
#   docker build -f analog-level-sensor/rust/Dockerfile \
#       -t ghcr.io/getdoover/analog-level-sensor-rs:main .
#
# (Once doover-rs is published, this can switch to a versioned dependency and
# the normal app-repo build context.)

FROM rust:1-alpine AS builder
RUN apk add --no-cache musl-dev protobuf-dev
# doover-proto's build script uses vendored protoc unless PROTOC is set; on
# Alpine (musl) point it at the system protoc.
ENV PROTOC=/usr/bin/protoc
WORKDIR /build
COPY doover-rs ./doover-rs
COPY analog-level-sensor/rust ./analog-level-sensor/rust
WORKDIR /build/analog-level-sensor/rust
RUN cargo build --release

# Fully static musl binary -> runs on scratch (final image ~2 MB).
FROM scratch
COPY --from=builder /build/analog-level-sensor/rust/target/release/analog-level-sensor /analog-level-sensor
ENTRYPOINT ["/analog-level-sensor"]
