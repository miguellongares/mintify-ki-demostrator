#!/usr/bin/env bash
#
# reset_rag.sh — full file/RAG reset for MINTIFY (DESTRUCTIVE).
#
# Wipes ALL uploaded files everywhere:
#   1. OWUI `file` + `user_file` tables    — drop every content row and grant
#   2. uploads directory                   — delete every stored file on disk
#   3. Qdrant `mintify_rag` collection      — wipe + recreate, clearing the
#                                             rag-backend's in-memory _indexed cache
#
# Ordering matters: rows first, then disk, then Qdrant last — so a stray ingest
# webhook can't recreate points from a row that still exists.
#
# Note: knowledge bases are NOT deleted; any KB that referenced these files will
# show dangling file_ids until you remove/recreate it. Run a clean upload after.
#
# Usage:  scripts/reset_rag.sh            (prompts for confirmation)
#         scripts/reset_rag.sh --yes      (skip the prompt)

set -euo pipefail

# Run from the repo root regardless of where the script is invoked from.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

WEBUI_CONTAINER="mintify-webui"
RAG_URL="http://localhost:8088"
UPLOADS_DIR="./data/open-webui/uploads"

if [[ "${1:-}" != "--yes" ]]; then
  echo "This will PERMANENTLY delete ALL uploaded files:"
  echo "  - every row in OWUI's file table"
  echo "  - every file in $UPLOADS_DIR"
  echo "  - the Qdrant '$RAG_URL' mintify_rag collection (wiped + recreated)"
  read -r -p "Type 'yes' to continue: " confirm
  [[ "$confirm" == "yes" ]] || { echo "Aborted."; exit 1; }
fi

echo "==> 1/3 Clearing OWUI file + user_file tables…"
docker exec "$WEBUI_CONTAINER" python -c \
  "from open_webui.models.files import Files, UserFiles; \
print('deleted_all_files:', Files.delete_all_files()); \
print('deleted_all_grants:', UserFiles.delete_all_grants())"

echo "==> 2/3 Emptying uploads directory ($UPLOADS_DIR)…"
if [[ -d "$UPLOADS_DIR" ]]; then
  find "$UPLOADS_DIR" -mindepth 1 -delete
  echo "uploads cleared"
else
  echo "uploads dir not found — skipping"
fi

echo "==> 3/3 Resetting Qdrant collection (and clearing the indexer cache)…"
curl -fsS -X POST "$RAG_URL/api/reset-collection" && echo

echo "Done. RAG state is empty — re-upload to repopulate."
