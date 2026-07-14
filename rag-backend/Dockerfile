FROM python:3.11-slim

WORKDIR /app

# PyTorch CPU must be installed BEFORE requirements.txt: sentence-transformers
# depends on torch, and on Linux pip would otherwise pull the CUDA build
# (~4 GB of nvidia-* wheels) — which fills the disk on CPU-only servers.
# Separate RUN to isolate --index-url from the rest of pip's resolver.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/uploads

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
