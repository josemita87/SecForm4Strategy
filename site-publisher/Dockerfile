FROM python:3.14-slim

# Bring in the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

# Debian security updates; pip goes, as uv manages the environment and pip's vendored packages carry CVEs
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && pip uninstall -y pip

# Repo layout replicated under /repo, built from the repo root (docs/development.md)
ENV SVC_DIR=/repo/site-publisher
ENV PYTHONPATH=${SVC_DIR}/src
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
COPY site-publisher/pyproject.toml site-publisher/uv.lock ${SVC_DIR}/

WORKDIR ${SVC_DIR}

# Install dependencies into the project environment
RUN uv sync --frozen --no-dev

# Copy the rest of the application code
COPY site-publisher ${SVC_DIR}/

# The Lambda runtime interface client runs handler.handle from src/; the function's image config repeats it
WORKDIR ${SVC_DIR}/src
ENTRYPOINT ["/repo/site-publisher/.venv/bin/python", "-m", "awslambdaric"]
CMD ["handler.handle"]
