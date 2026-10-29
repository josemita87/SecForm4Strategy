FROM python:3.14-slim

# Bring in the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

# Install essential tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Chromium and its dependencies (for arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    libappindicator3-1 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxrandr2 \
    libxi6 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libgdk-pixbuf2.0-0 \
    libgtk-3-0 \
    libasound2 \
    libnspr4 \
    libxss1 \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for Chromium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_BIN=/usr/bin/chromedriver
ENV DISPLAY=:99

# Repo layout replicated under /repo, built from the repo root (docs/development.md)
ENV SVC_DIR=/repo/sec-feature-pipeline/master-index
# Add src to the PYTHONPATH
ENV PYTHONPATH=${SVC_DIR}
ENV PYTHONUNBUFFERED=1
# uv: compile bytecode and copy packages into the environment
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Clients package and dependency manifests first, so the dependency layer is cached
COPY packages/secform4strategy-clients /repo/packages/secform4strategy-clients
COPY sec-feature-pipeline/master-index/pyproject.toml sec-feature-pipeline/master-index/uv.lock ${SVC_DIR}/

WORKDIR ${SVC_DIR}

# Install dependencies into the project environment
RUN uv sync --frozen --no-dev

# Copy the rest of the application code
COPY sec-feature-pipeline/master-index ${SVC_DIR}/

# Default command
CMD ["uv", "run", "--no-sync", "python", "src/main.py"]
