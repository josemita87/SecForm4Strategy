FROM python:3.14-slim

# Bring in the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

# Service lives at this path inside the image; the repo layout is replicated so
# the [tool.uv.sources] path to the shared clients package resolves the same way
# it does on the host. This image is built with the repo root as the build context.
ENV SVC_DIR=/repo/sec-feature-pipeline/api-enricher
# Add src to the PYTHONPATH
ENV PYTHONPATH=${SVC_DIR}
ENV PYTHONUNBUFFERED=1
# uv: compile bytecode and copy packages into the environment
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the shared clients package (its path source is resolved relative to the
# service pyproject) and the service's dependency manifests first, for caching.
COPY packages/secform4strategy-clients /repo/packages/secform4strategy-clients
COPY sec-feature-pipeline/api-enricher/pyproject.toml sec-feature-pipeline/api-enricher/uv.lock ${SVC_DIR}/

WORKDIR ${SVC_DIR}

# Install dependencies into the project environment
RUN uv sync --frozen --no-dev

# Copy the rest of the application code
COPY sec-feature-pipeline/api-enricher ${SVC_DIR}/

# Run the application
CMD ["uv", "run", "--no-sync", "python", "src/main.py"]
