FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install compilation and postgres system libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python package dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Install Playwright headless browser (Chromium) and its system dependencies
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application source code
COPY . .

# Expose API port
EXPOSE 8000
