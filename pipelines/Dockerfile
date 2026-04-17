FROM ghcr.io/open-webui/pipelines:main

WORKDIR /app

# RAG dependencies (normal PyPI packages first)
RUN pip install --no-cache-dir \
    "pymupdf>=1.24.0" \
    "sentence-transformers>=2.7.0" \
    "qdrant-client>=1.9.0" \
    "openai>=1.0.0"

# PyTorch MUST be a separate RUN — mixing --index-url poisons other packages
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

RUN mkdir -p /app/uploads

# Note: embedding model downloads on first startup (~30s one-time delay)
