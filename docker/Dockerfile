# Sandbox image for migration-agent workers.
# - non-root user, no credentials baked in (API keys are injected at runtime via env/secret)
# - designed for `--read-only` root fs: only /tmp and /scratch (tmpfs) are written
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/agent

RUN groupadd --gid 10001 agent \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /home/agent --shell /usr/sbin/nologin agent \
 && mkdir -p /opt/migration-agent /workspace /scratch /home/agent \
 && chown agent:agent /scratch /home/agent

# Install the package (code baked into the image, root-owned => not writable by the agent user).
COPY pyproject.toml /opt/migration-agent/pyproject.toml
COPY src /opt/migration-agent/src
RUN pip install /opt/migration-agent && rm -rf /root/.cache

# /workspace is the repository under analysis; mount it read-only at runtime.
WORKDIR /workspace
USER 10001:10001

# No ENV for keys/tokens on purpose.
ENTRYPOINT ["migration-agent"]
