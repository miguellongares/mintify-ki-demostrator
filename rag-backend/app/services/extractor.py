"""
Extractor — the SAME extraction method OWUI uses for knowledge-base/chat files
------------------------------------------------------------------------------
OWUI runs with CONTENT_EXTRACTION_ENGINE=docling: every uploaded file that is
not plain text is converted by a docling-serve instance (running on RunPod,
SSH-tunneled to host port 5001 — see scripts/init_models.sh).

Personal files never pass through OWUI's loaders, so this service replicates
OWUI's Loader behaviour 1:1 (open-webui-src/backend/open_webui/retrieval/
loaders/main.py):

  - text-like files (extension in KNOWN_SOURCE_EXT or a text/* content type)
    are decoded directly — OWUI uses TextLoader for these, not docling
  - everything else → POST {DOCLING_SERVER_URL}/v1/convert/file
      multipart field "files", form params
      {"image_export_mode": "placeholder", "table_mode": "accurate",
       "do_ocr", "force_ocr", "ocr_lang", "ocr_preset"}
      → response document.md_content (markdown, incl. reconstructed tables)
      OCR params are explicit rather than left to docling-serve's own
      defaults (see config.py docling_* settings for why: force_ocr must
      stay FALSE — it discards the PDF's embedded text layer, gluing words
      together and destroying German umlauts — and ocr_preset must stay
      "auto", this deployment 404s on any other preset value).
  - the result is passed through ftfy.fix_text(), same as OWUI's Loader.load()

If docling is unreachable (tunnel down), the file is still stored and
downloadable — it just isn't indexed. POST /api/personal-docs/sync (admin)
backfills the extraction once the service is back.
"""

import ftfy
import httpx

# Copied 1:1 from OWUI retrieval/loaders/main.py (known_source_ext): the
# extensions OWUI treats as plain text, bypassing the extraction engine.
KNOWN_SOURCE_EXT = {
    "go", "py", "java", "sh", "bat", "ps1", "cmd", "js", "ts", "css",
    "cpp", "hpp", "h", "c", "cs", "sql", "log", "ini", "pl", "pm",
    "r", "dart", "dockerfile", "env", "php", "hs", "hsc", "lua",
    "nginxconf", "conf", "m", "mm", "plsql", "perl", "rb", "rs",
    "db2", "scala", "bash", "swift", "vue", "svelte", "msg", "ex",
    "exs", "erl", "tsx", "jsx", "lhs", "json",
}

# docling can take a while on large/scanned PDFs (OWUI's requests.post has no
# timeout at all); generous but bounded.
DOCLING_TIMEOUT_SECONDS = 300.0


class Extractor:
    def __init__(
        self,
        docling_url: str,
        do_ocr: bool = True,
        force_ocr: bool = False,
        ocr_lang: str = "de",
        ocr_preset: str = "auto",
    ):
        self._docling_url = docling_url.rstrip("/")
        self._do_ocr = do_ocr
        self._force_ocr = force_ocr
        self._ocr_lang = ocr_lang
        self._ocr_preset = ocr_preset

    def is_available(self) -> bool:
        """True if docling-serve answers. Checked before a bulk re-extraction so
        the common failure (RunPod tunnel down) is reported up front instead of
        after every file has already failed."""
        try:
            r = httpx.get(f"{self._docling_url}/health", timeout=5.0)
            return r.status_code == 200
        except Exception as e:
            print(f"[RAG] docling-serve unreachable at {self._docling_url}: {e}", flush=True)
            return False

    def extract(self, filename: str, content_type: str | None, data: bytes) -> str:
        try:
            if self._is_text_file(filename, content_type):
                text = data.decode("utf-8", errors="replace")
            else:
                text = self._docling_convert(filename, content_type, data)
        except Exception as e:
            print(f"[PersonalDocs] extraction failed for {filename!r}: {e}", flush=True)
            return ""
        # OWUI's Loader.load() runs every result through ftfy (mojibake repair).
        text = ftfy.fix_text(text).strip()
        if not self._is_text_file(filename, content_type) and len(text) < 20:
            print(
                f"[PersonalDocs] suspiciously little text extracted from {filename!r} "
                f"({len(text)} chars) — possible OCR failure",
                flush=True,
            )
        return text

    @staticmethod
    def _is_text_file(filename: str, content_type: str | None) -> bool:
        # Same check as OWUI's Loader._is_text_file().
        file_ext = (filename or "").split(".")[-1].lower()
        return file_ext in KNOWN_SOURCE_EXT or (
            content_type is not None and content_type.find("text/") >= 0
        )

    def _docling_convert(self, filename: str, content_type: str | None, data: bytes) -> str:
        # Same wire format as OWUI's DoclingLoader.load(): multipart "files"
        # (the filename's extension tells docling the format), form params for
        # image/table handling, markdown taken from document.md_content.
        files = {"files": (filename, data, content_type or "application/octet-stream")}
        params = {
            "image_export_mode": "placeholder",
            "table_mode": "accurate",
            "do_ocr": str(self._do_ocr).lower(),
            "force_ocr": str(self._force_ocr).lower(),
            "ocr_lang": self._ocr_lang,
            "ocr_preset": self._ocr_preset,
        }

        response = httpx.post(
            f"{self._docling_url}/v1/convert/file",
            files=files,
            data=params,
            timeout=DOCLING_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            error_msg = f"Error calling Docling API: {response.reason_phrase}"
            try:
                detail = response.json().get("detail")
                if detail:
                    error_msg += f" - {detail}"
            except Exception:
                if response.text:
                    error_msg += f" - {response.text[:500]}"
            raise Exception(error_msg)

        document_data = response.json().get("document") or {}
        return document_data.get("md_content") or ""
