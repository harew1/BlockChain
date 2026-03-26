# ─── CryptoTrader Pro — Dockerfile ────────────────────────────────────────────
FROM python:3.11-slim

# Metadata
LABEL maintainer="CryptoTrader Pro"
LABEL description="Production crypto trading bot platform"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create required directories
RUN mkdir -p logs models

# Non-root user for security
RUN useradd -m -u 1000 trader && chown -R trader:trader /app
USER trader

# Default: run FastAPI backend
# Override CMD in docker-compose for Streamlit panel.
EXPOSE 8000 8501

CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
