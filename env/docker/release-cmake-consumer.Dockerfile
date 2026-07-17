FROM ubuntu@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cmake=3.28.3-1build7 \
        g++-13=13.3.0-6ubuntu2~24.04.1 \
        gcc-13=13.3.0-6ubuntu2~24.04.1 \
        ninja-build=1.11.1-2 \
    && rm -rf /var/lib/apt/lists/*

ENV CC=/usr/bin/gcc-13 \
    CXX=/usr/bin/g++-13
