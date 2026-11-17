# Rust build of the analog level sensor. Build from the repo root:
#
#   docker build -f rust/Dockerfile -t analog-level-sensor-rs:dev .
#
# doover-rs is a pinned git dependency (rust/Cargo.toml), so no sibling
# checkout is needed in the build context.

FROM rust:1-alpine AS builder
# musl-dev: static linking. protobuf-dev: doover-proto's build script needs
# protoc (on Alpine/musl the vendored protoc isn't used). git: the doover-rs
# git dependency.
RUN apk add --no-cache musl-dev protobuf-dev git
ENV PROTOC=/usr/bin/protoc
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
WORKDIR /build
COPY rust/ ./
RUN cargo build --release --locked

# Fully static musl binary -> runs on scratch (image of a few MB).
FROM scratch
# The same labels as the Python image, so the device agent manages it alike.
LABEL com.doover.app="true"
LABEL com.doover.managed="true"
COPY --from=builder /build/target/release/analog-level-sensor /analog-level-sensor
# A scratch image has no curl: the binary probes its own healthcheck server.
HEALTHCHECK --interval=30s --timeout=2s --start-period=5s CMD ["/analog-level-sensor", "healthcheck"]
ENTRYPOINT ["/analog-level-sensor"]
