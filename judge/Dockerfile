# Image used to compile and run contestant code. Build with:
#   docker build -t cms-judge judge/
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ default-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user; the container is also started with no network, a read-only root and dropped capabilities
RUN useradd --uid 1000 --create-home judge
USER judge
WORKDIR /work
