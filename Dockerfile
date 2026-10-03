# PolicyForge as a container: the CLI, and the MCP server over stdio.
#
#   docker build -t policyforge:local .
#   docker run -i --rm policyforge:local mcp
#
# README.md ("Running in a container") covers the mounts: your config, topic
# registry, licensed content and output directory come in at run time, never
# at build time, and credentials come in as environment variables.
#
# The base image is pinned by digest and Dependabot moves it. The same digest
# is used by .devcontainer/Dockerfile and scripts/ci_in_docker.py, and
# tests/test_container.py fails if they drift apart.

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Every dependency from the hashed runtime lock: the project's dependencies and
# the mcp extra, at the versions CI tests, with none of the dev tools.
COPY requirements/runtime.txt /tmp/runtime.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes -r /tmp/runtime.txt

# The crosswalk is a build artifact, gitignored in the repository, and three
# commands (addresses, coverage, synthesize) read it. It is derived entirely
# from the bundled catalogs, so it is built here rather than shipped.
WORKDIR /app
COPY src ./src
COPY data/frameworks ./data/frameworks
RUN PYTHONPATH=/app/src /opt/venv/bin/python -m policyforge.cli map \
      --controls data/frameworks/nist-800-53-r5/controls.json \
      --controls data/frameworks/fedramp/controls.json \
      --controls data/frameworks/arc-ampe/controls.json \
      --controls data/frameworks/hipaa-security-rule/controls.json \
      --out data/frameworks/crosswalk.json


FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

LABEL org.opencontainers.image.title="policyforge" \
      org.opencontainers.image.source="https://github.com/rdazzleman/policyforge" \
      org.opencontainers.image.licenses="Apache-2.0"

# The package runs from source on PYTHONPATH rather than being installed as a
# wheel. Building a wheel needs setuptools from an unhashed build-isolation
# download, and every dependency here is already hashed. Every path the CLI
# reads (config/, data/frameworks/, output/) is relative to the working
# directory, so the working directory is the repository layout.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin policyforge

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY LICENSE ./
COPY src ./src
COPY --from=build /app/data/frameworks ./data/frameworks
COPY config/config.example.yaml config/topics.example.yaml ./config/

# The one place the process writes by default: drafts, the model-call ledger,
# version history and the synced corpus. Mount a volume here to keep them.
RUN mkdir -p /app/output && chown policyforge:policyforge /app/output

USER policyforge

# Called with prog_name so help and errors say `policyforge`, as the installed
# command does, rather than `python -m policyforge.cli`.
ENTRYPOINT ["python", "-c", "import sys; from policyforge.cli import cli; sys.exit(cli(prog_name='policyforge'))"]
CMD ["--help"]
