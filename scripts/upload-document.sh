#!/bin/bash
# Upload a PDF into the MINTIFY pipeline so it gets indexed for RAG.
# Usage: ./scripts/upload-document.sh path/to/document.pdf

set -e

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "Usage: ./scripts/upload-document.sh <path/to/file.pdf>"
  exit 1
fi

FILENAME=$(basename "$FILE")
docker cp "$FILE" mintify-pipelines:/app/uploads/"$FILENAME"
echo "Uploaded '$FILENAME' — it will be indexed on the next chat message."
