# Use Python slim image
FROM python:3.12-slim

# Install essential tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Chromium and its dependencies (for arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \
#    python3-setuptools \
    chromium=132.0.6834.83-1~deb12u1 \
    chromium-driver=132.0.6834.83-1~deb12u1 \
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

# Install Python build tools and dependencies
RUN pip install --no-cache-dir poetry==1.8.3

# Set working directory
WORKDIR /app

# Copy application files
COPY . /app

# Install dependencies using Poetry
RUN poetry install --no-interaction --no-ansi --verbose

# Default command
CMD ["poetry", "run", "python", "src/main.py"]