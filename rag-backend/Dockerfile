FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# PyTorch CPU in a separate RUN to isolate --index-url from the rest of pip's resolver
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN mkdir -p /app/uploads

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
