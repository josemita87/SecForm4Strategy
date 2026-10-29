FROM python:3.14-slim

# Bring in the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

# Repo layout replicated under /repo, built from the repo root (docs/development.md)
ENV SVC_DIR=/repo/training
# Add src to the PYTHONPATH
ENV PYTHONPATH=${SVC_DIR}
ENV PYTHONUNBUFFERED=1
# uv: compile bytecode and copy packages into the environment
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install necessary build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Rust and Cargo (needed to build some native wheels)
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Clients package and dependency manifests first, so the dependency layer is cached
COPY packages/secform4strategy-clients /repo/packages/secform4strategy-clients
COPY training/pyproject.toml training/uv.lock ${SVC_DIR}/

WORKDIR ${SVC_DIR}

# Install dependencies into the project environment
RUN uv sync --frozen --no-dev

# Copy the rest of the source code into the container
COPY training ${SVC_DIR}/

# Define the command to run the application
CMD ["uv", "run", "--no-sync", "python", "src/main.py"]
