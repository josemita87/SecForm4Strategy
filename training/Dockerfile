# Use Python slim image
FROM python:3.12-slim

# Install necessary dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy only the requirements file first to leverage Docker cache
COPY requirements.txt /app/

# Install Python dependencies (this layer is cached unless requirements.txt changes)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source code into the container
COPY . /app

# Expose any necessary ports (if needed)
EXPOSE 54321

# Define the command to run your application
CMD ["python", "/app/src/main.py"]