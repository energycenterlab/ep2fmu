# syntax=docker/dockerfile:1.7

ARG UBUNTU_VERSION=24.04
ARG RUST_VERSION=1.86
ARG EP2FMU_DOCKER_PLATFORM=linux/amd64

FROM --platform=${EP2FMU_DOCKER_PLATFORM} rust:${RUST_VERSION}-bookworm AS rust-builder

ARG ENERGYPLUS_VERSION=26.1.0
ENV EP2FMU_ENERGYPLUS_VERSION=${ENERGYPLUS_VERSION}

WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY runtime ./runtime
RUN cargo build --locked --release \
    --package ep2fmu-fmi2 \
    --package ep2fmu-worker


FROM --platform=${EP2FMU_DOCKER_PLATFORM} ubuntu:${UBUNTU_VERSION} AS python-builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY scripts/package_runtime.py ./scripts/package_runtime.py
COPY --from=rust-builder /src/target/release/libep2fmu_fmi2.so /runtime/libep2fmu_fmi2.so
COPY --from=rust-builder /src/target/release/ep2fmu-worker /runtime/ep2fmu-worker
RUN python3 scripts/package_runtime.py \
        --platform linux64 \
        --target-dir /runtime \
    && python3 -m venv /opt/ep2fmu \
    && /opt/ep2fmu/bin/pip install --no-cache-dir .


FROM --platform=${EP2FMU_DOCKER_PLATFORM} ubuntu:${UBUNTU_VERSION} AS energyplus

ARG ENERGYPLUS_VERSION=26.1.0
ARG ENERGYPLUS_BUILD=6f2e40d102
ARG ENERGYPLUS_PLATFORM=Linux-Ubuntu24.04-x86_64
ARG ENERGYPLUS_SHA256=b651f4197bfc147a0f66dc92c58895d1748bdadb7a0288145fa9d50375edfbca
ARG ENERGYPLUS_RELEASE_BASE=https://github.com/NatLabRockies/EnergyPlus/releases/download

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/energyplus
RUN asset="EnergyPlus-${ENERGYPLUS_VERSION}-${ENERGYPLUS_BUILD}-${ENERGYPLUS_PLATFORM}.tar.gz" \
    && curl --fail --location --retry 3 \
        "${ENERGYPLUS_RELEASE_BASE}/v${ENERGYPLUS_VERSION}/${asset}" \
        --output energyplus.tar.gz \
    && echo "${ENERGYPLUS_SHA256}  energyplus.tar.gz" | sha256sum --check - \
    && mkdir -p /opt/energyplus \
    && tar --extract --gzip --file energyplus.tar.gz \
        --directory /opt/energyplus --strip-components=1


FROM --platform=${EP2FMU_DOCKER_PLATFORM} ubuntu:${UBUNTU_VERSION} AS runtime

ARG ENERGYPLUS_VERSION=26.1.0
ARG ENERGYPLUS_BUILD=6f2e40d102

LABEL org.opencontainers.image.title="ep2fmu" \
      org.opencontainers.image.description="Portable FMI 2.0 Co-Simulation exporter for EnergyPlus" \
      org.opencontainers.image.licenses="BSD-3-Clause" \
      org.opencontainers.image.version="1.0.0" \
      io.ep2fmu.compatibility.energyplus="${ENERGYPLUS_VERSION}-${ENERGYPLUS_BUILD}" \
      io.ep2fmu.compatibility.fmi="2.0 Co-Simulation"

ENV DEBIAN_FRONTEND=noninteractive \
    ENERGYPLUS_HOME=/opt/energyplus \
    EP2FMU_ENERGYPLUS_VERSION=${ENERGYPLUS_VERSION} \
    EP2FMU_DEFAULT_PLATFORM=linux64 \
    PATH=/opt/ep2fmu/bin:/opt/energyplus:${PATH}

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        libgomp1 \
        libx11-6 \
        python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ep2fmu \
    && useradd --uid 10001 --gid 10001 --create-home ep2fmu

COPY --from=python-builder /opt/ep2fmu /opt/ep2fmu
COPY --from=energyplus /opt/energyplus /opt/energyplus

WORKDIR /work
USER ep2fmu

ENTRYPOINT ["ep2fmu"]
CMD ["--help"]
