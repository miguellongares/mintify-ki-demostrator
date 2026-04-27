FROM ghcr.io/open-webui/pipelines:main

WORKDIR /app

# Only httpx needed — all ML dependencies live in mintify-rag-service
RUN pip install --no-cache-dir "httpx>=0.27.0"
