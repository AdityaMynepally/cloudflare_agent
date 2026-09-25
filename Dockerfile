# Dockerfile for Web Audit Backend
# No browser/Playwright dependencies needed - extension handles browsing.
# Node.js + Chromium ARE needed here though, for the real Lighthouse
# performance-scoring phase (backend/ai/analysis/lighthouse.py shells out to
# `npx lighthouse`, which in turn launches a real Chrome via chrome-launcher).

FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    API_PORT=8000 \
    CHROME_PATH=/usr/bin/chromium

# Install curl (healthcheck), Node.js (npx/Lighthouse), and Chromium
# (the browser Lighthouse actually drives).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    chromium \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Pre-install Lighthouse at build time so `npx lighthouse@12` resolves
# instantly and offline at runtime instead of hitting the npm registry on
# every container's first audit (Azure's egress can be slow/locked down).
RUN npm install -g lighthouse@12

# Copy backend requirements and install
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code and UI
COPY backend/ .
COPY ui/ /app/ui/

# Create directories
RUN mkdir -p /app/results /app/screenshots

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
