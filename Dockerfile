# Use Python 3.11 slim image for smaller size
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables for memory optimization
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    MALLOC_ARENA_MAX=2 \
    PYTHONOPTIMIZE=1

# Install system dependencies (including curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies - ensure gunicorn is installed
RUN pip install --no-cache-dir --no-compile -r requirements.txt && \
    pip install --no-cache-dir gunicorn==21.2.0

# Verify gunicorn is installed
RUN which gunicorn && gunicorn --version

# Copy application
COPY . .

# Create directories
RUN mkdir -p /app/static/uploads /app/data /app/instance && \
    chmod -R 755 /app/static/uploads /app/data /app/instance

# Create non-root user
RUN addgroup --system app && adduser --system --group app && \
    chown -R app:app /app

USER app

EXPOSE 5000

# Health check - using python's built-in HTTP server for health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run with Gunicorn - using full path to ensure it's found
CMD ["/usr/local/bin/gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "2", "--timeout", "60", "--graceful-timeout", "30", "--keep-alive", "5", "--max-requests", "1000", "--max-requests-jitter", "50", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "app:app"]