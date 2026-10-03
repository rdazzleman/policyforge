# The development container: the environment CI runs in (Linux, Python 3.12),
# so `python scripts/check.py` here fails on what CI would fail on. The
# Windows gate does not catch everything: CRLF-sensitive hashes and 3.12-only
# behaviour have both reached main that way.
#
# The same base digest as the runtime Dockerfile and scripts/ci_in_docker.py;
# tests/test_container.py keeps the three in step.

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# git for the content-tree tests, which shell out to it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# The environments live outside the workspace. The workspace is a bind mount
# of the host checkout, and a Linux venv written into it would replace the
# host's own .venv.
#
# semgrep gets its own environment, and only its entry point goes on PATH
# (through /opt/semgrep-bin), so its pinned dependencies stay out of the
# project environment. The link dangles until postCreateCommand installs it.
RUN useradd --create-home --uid 1000 --shell /bin/bash dev \
 && mkdir -p /opt/venv /opt/semgrep /opt/semgrep-bin \
 && ln -s /opt/semgrep/bin/semgrep /opt/semgrep-bin/semgrep \
 && chown dev:dev /opt/venv /opt/semgrep

USER dev
ENV PATH=/opt/venv/bin:$PATH:/opt/semgrep-bin \
    PIP_DISABLE_PIP_VERSION_CHECK=1
