# syntax=docker/dockerfile:1
#
# The analog level sensor's image: a static musl binary on scratch.
#
# Cross-compiled like the platform interface and the device agent: one native
# rustc, zig as the cross C compiler/linker, no QEMU. CI builds the platforms in
# doover_config.json (amd64 and arm64).
#
# Build one arch locally, from this directory:
#   docker buildx build --platform linux/arm64 -t analog-level-sensor:dev --load .
#
# doover-rs is a pinned git dependency (Cargo.toml), so no sibling checkout is
# needed in the build context.

ARG ZIG_VERSION=0.13.0

## BUILD STAGE (runs on $BUILDPLATFORM, cross-compiles to $TARGETPLATFORM) ##
FROM --platform=$BUILDPLATFORM rust:1-bookworm AS builder
ARG ZIG_VERSION
ARG TARGETPLATFORM
ARG TARGETARCH
ARG TARGETVARIANT

# No protoc here: doover-proto's build script falls back to a vendored one.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xz-utils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "$(uname -m)" in \
        aarch64) ZARCH=aarch64 ;; \
        x86_64)  ZARCH=x86_64  ;; \
        *) echo "unsupported build arch $(uname -m)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://ziglang.org/download/${ZIG_VERSION}/zig-linux-${ZARCH}-${ZIG_VERSION}.tar.xz" -o /tmp/zig.tar.xz; \
    mkdir -p /opt/zig; tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1; \
    ln -s /opt/zig/zig /usr/local/bin/zig; \
    zig version
RUN cargo install cargo-zigbuild --locked

RUN set -eux; \
    case "$TARGETPLATFORM" in \
        linux/amd64)  TRIPLE=x86_64-unknown-linux-musl      ;; \
        linux/arm64)  TRIPLE=aarch64-unknown-linux-musl     ;; \
        linux/arm/v7) TRIPLE=armv7-unknown-linux-musleabihf ;; \
        *) echo "unsupported target platform: $TARGETPLATFORM" >&2; exit 1 ;; \
    esac; \
    echo "$TRIPLE" > /tmp/triple; \
    rustup target add "$TRIPLE"

WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src/ ./src/
# Caches scoped per-arch (id) so concurrent multi-platform builds don't race on
# unpacking the same crate into a shared registry.
RUN --mount=type=cache,target=/usr/local/cargo/registry,id=als-registry-${TARGETARCH}${TARGETVARIANT} \
    --mount=type=cache,target=/build/target,id=als-target-${TARGETARCH}${TARGETVARIANT} \
    set -eux; \
    TRIPLE="$(cat /tmp/triple)"; \
    cargo zigbuild --release --locked --target "$TRIPLE"; \
    cp "target/${TRIPLE}/release/analog-level-sensor" /analog-level-sensor

## FINAL IMAGE ##
FROM scratch
# The labels the device agent manages app containers by.
LABEL com.doover.app="true"
LABEL com.doover.managed="true"
COPY --from=builder /analog-level-sensor /analog-level-sensor
# A scratch image has no curl: the binary probes its own healthcheck server.
HEALTHCHECK --interval=30s --timeout=2s --start-period=5s CMD ["/analog-level-sensor", "healthcheck"]
ENTRYPOINT ["/analog-level-sensor"]
