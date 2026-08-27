import json
import os
import re
from typing import Generator

import httpx
from pydantic import BaseModel


# OWUI's built-in RAG wraps the user turn with a "### Task: Respond..." template plus
# <source id="N">…</source> blocks from its own retrieval. We do our own retrieval in
# the rag-backend, so we strip both before forwarding.
_SOURCE_RE = re.compile(r"<source\b[^>]*>.*?</source>", re.DOTALL | re.IGNORECASE)
_CONTEXT_BLOCK_RE = re.compile(r"<context\b[^>]*>.*?</context>", re.DOTALL | re.IGNORECASE)
_USER_QUERY_RE = re.compile(r"<user_query\b[^>]*>(.*?)</user_query>", re.DOTALL | re.IGNORECASE)


def _is_meta_task(msg: str) -> bool:
    """OWUI's internal task templates all start with '### Task:'."""
    return msg.lstrip().startswith("### Task:")


def _clean_user_message(msg: str) -> str:
    """Undo OWUI's RAG_TEMPLATE wrapping, leaving just the user's real question."""
    # If OWUI wrapped the question in <user_query>…</user_query>, that IS the question.
    uq = _USER_QUERY_RE.search(msg)
    if uq:
        return uq.group(1).strip()
    # Otherwise, just rip out any <context>/<source> blocks that snuck in.
    cleaned = _CONTEXT_BLOCK_RE.sub("", msg)
    cleaned = _SOURCE_RE.sub("", cleaned)
    return cleaned.strip()


def _strip_injected_context(text: str) -> str:
    """Remove OWUI's injected file context from the system message.

    With BYPASS_EMBEDDING_AND_RETRIEVAL on, OWUI injects the full text of any
    attached file into the system message, wrapped as
    <context>…<source>…</source>…</context> (OWUI's DEFAULT_RAG_TEMPLATE). We do
    retrieval ourselves in the rag-backend, so strip those blocks before
    forwarding — otherwise the document text would reach vLLM twice (once here,
    once via the rag-backend's [KONTEXT]). The admin's persona text is preserved.
    """
    cleaned = _CONTEXT_BLOCK_RE.sub("", text)
    cleaned = _SOURCE_RE.sub("", cleaned)
    return cleaned.strip()


def _extract_file_ids(body: dict, messages: list) -> list[str]:
    """
    Pull the OWUI file IDs the user attached as Knowledge to the active model.
    OWUI stores them at body.metadata.model.info.meta.knowledge[*].files[*].id
    (each "knowledge" entry is a collection containing files). Single-file
    attachments live as entries with type=="file" and their id at the top of
    the knowledge entry. Direct per-message uploads can also appear at
    metadata.files.

    Always returns a list (STRICT scoping): the model's attached Knowledge + any
    files dropped in THIS chat. An empty [] means "nothing attached" → the backend
    retrieves nothing and the model answers on its own. We never return None /
    "search the whole index", because that leaked files dropped in one chat into
    other chats when no KB was attached.
    """
    #Understand the body structure in the pipeline:
    # The body contains all the possible information:
    # system prompt,query, old querys and answers, context, model, etc..
    if not isinstance(body, dict):
        return []
    #Inside the body we get from OWUI, the meta is extracted:
    #which contains information like: user_id, chat_id, tool_ids, etc...
    #one of the information is the current selected model like KI-Experte
    meta = body.get("metadata")
    if not isinstance(meta, dict):
        return []

    ids: list[str] = []
    saw_knowledge_field = False

    # 1) Per-model Knowledge attachments.
    model = meta.get("model")
    if isinstance(model, dict):
        info = model.get("info")
        if isinstance(info, dict):
            mmeta = info.get("meta")
            if isinstance(mmeta, dict) and "knowledge" in mmeta:
                saw_knowledge_field = True
                for k in mmeta.get("knowledge") or []:
                    if not isinstance(k, dict):
                        continue
                    if k.get("type") == "file" and k.get("id"):
                        ids.append(str(k["id"]))
                    for sub in k.get("files") or []:
                        if isinstance(sub, dict) and sub.get("id"):
                            ids.append(str(sub["id"]))

    # 2) Per-message file uploads (drag-and-drop into the chat input).
    # It is important to understand how the drag-and-drop files are read!!
    msg_files = meta.get("files")
    if isinstance(msg_files, list):
        saw_knowledge_field = True
        for f in msg_files:
            if isinstance(f, dict) and f.get("id"):
                ids.append(str(f["id"]))
            if isinstance(f, dict):
                for sub in f.get("files") or []:
                    if isinstance(sub, dict) and sub.get("id"):
                        ids.append(str(sub["id"]))

    if not saw_knowledge_field:
        # No Knowledge field and no message uploads → nothing attached → no retrieval.
        return []
    # Dedupe while preserving order.
    return list(dict.fromkeys(ids))


def _extract_user_id(body: dict) -> str | None:
    """OWUI id of the person chatting — the rag-backend uses it to include the
    user's OWN personal files (sidebar feature) in retrieval, scoped so no other
    user's documents can ever surface. OWUI puts it at body.metadata.user_id;
    body.user.id is kept as a fallback."""
    if not isinstance(body, dict):
        return None
    meta = body.get("metadata")
    if isinstance(meta, dict) and meta.get("user_id"):
        return str(meta["user_id"])
    user = body.get("user")
    if isinstance(user, dict) and user.get("id"):
        return str(user["id"])
    return None


def _extract_chat_id(body: dict) -> str | None:
    """OWUI chat id (body.metadata.chat_id). The rag-backend caches this turn's
    retrieval/web context under it so the Bewertung judges can grade against the
    same sources."""
    meta = body.get("metadata") if isinstance(body, dict) else None
    if isinstance(meta, dict) and meta.get("chat_id"):
        return str(meta["chat_id"])
    return None


def _web_search_enabled(body: dict) -> bool:
    """True when the user toggled 🌐 web search for this turn.

    OWUI signals it at body.metadata.features.web_search. OWUI's own web fetch is
    disabled (MINTIFY_BACKEND_WEB_SEARCH=true in the open-webui container), so this
    flag is just the trigger: the rag-backend does the actual search + extraction.
    """
    meta = body.get("metadata") if isinstance(body, dict) else None
    feats = meta.get("features") if isinstance(meta, dict) else None
    return bool(feats.get("web_search")) if isinstance(feats, dict) else False


def _thinking_enabled(body: dict) -> bool:
    """True when the user toggled 💡 "Nachdenken" for this turn.

    OWUI forwards the whole features dict verbatim into body.metadata.features
    (main.py), so this custom flag rides alongside web_search. The rag-backend
    turns it into Qwen3.5's enable_thinking chat-template kwarg.
    """
    meta = body.get("metadata") if isinstance(body, dict) else None
    feats = meta.get("features") if isinstance(meta, dict) else None
    return bool(feats.get("thinking")) if isinstance(feats, dict) else False


def _kompetenz_capability_enabled(body: dict) -> bool:
    """False only when the admin explicitly unchecked the "Kompetenzniveau"
    capability box for this model (OWUI Admin Panel → Models → Capabilities).
    Missing key — a model saved before this checkbox existed — defaults to
    True so existing behavior is unaffected until an admin opts a model out.
    """
    if not isinstance(body, dict):
        return True
    meta = body.get("metadata")
    model = meta.get("model") if isinstance(meta, dict) else None
    info = model.get("info") if isinstance(model, dict) else None
    mmeta = info.get("meta") if isinstance(info, dict) else None
    capabilities = mmeta.get("capabilities") if isinstance(mmeta, dict) else None
    if not isinstance(capabilities, dict) or "kompetenzniveau" not in capabilities:
        return True
    return bool(capabilities["kompetenzniveau"])


def _log_payload(path: str, payload: dict) -> None:
    """Print the outgoing payload in a compact, readable way before _stream().

    A deep copy is logged with base64 image data URIs shortened to
    'data:image/...;base64,<NNN bytes>' so image attachments are visible in the
    log without flooding it with megabytes of base64.
    """
    def _shorten(value):
        if isinstance(value, dict):
            return {k: _shorten(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_shorten(v) for v in value]
        if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
            head, b64 = value.split(";base64,", 1)
            return f"{head};base64,<{len(b64)} chars>"
        return value

    pretty = json.dumps(_shorten(payload), indent=2, ensure_ascii=False)
    print(
        f"\n{'='*60}\n[MINTIFY] → POST {path}\n{'='*60}\n{pretty}\n{'='*60}\n",
        flush=True,
    )


class Pipeline:

    class Valves(BaseModel):
        BACKEND_URL: str = os.getenv("RAG_BACKEND_URL", "http://mintify-rag-service:8080")
        TOP_K: int = 5
        # Pipeline fallback persona. Used only when the OWUI model has no system prompt.
        # May contain {context} — if absent, the backend appends a [KONTEXT] block automatically.
        SYSTEM_PROMPT: str = (
            "Du bist ein hilfreicher KI-Assistent für finanzielle Bildung, entwickelt im Rahmen "
            "des MINTIFY-Forschungsprojekts der Hochschule Bonn-Rhein-Sieg.\n\n"
            "Beantworte alle Fragen ausschließlich auf Deutsch, klar und verständlich.\n\n"
            "Nutze den folgenden Kontext aus den hinterlegten Dokumenten, um die Frage des Nutzers "
            "zu beantworten:\n\n"
            "[KONTEXT]\n{context}\n[/KONTEXT]\n\n"
            "Falls die Antwort nicht im Kontext zu finden ist, weise ehrlich darauf hin und "
            "beantworte die Frage trotzdem so gut wie möglich auf Basis deines Wissens. "
            "Gib bei Verwendung von Kontextinformationen stets die Quelldokumente an."
        )

    def __init__(self):
        self.id = "mintify_pipeline"
        self.name = "MINTIFY"
        self.type = "manifold"
        self.valves = self.Valves(**self._load_valves())

    def pipelines(self) -> list:
        try:
            resp = httpx.get(f"{self.valves.BACKEND_URL}/api/models", timeout=5)
            resp.raise_for_status()
            result = [
                {"id": m["id"].replace("/", "__"), "name": m["name"]}
                for m in resp.json()
            ]
            print(f"[MINTIFY] Registered models: {[m['id'] for m in result]}", flush=True)
            return result
        except Exception as e:
            print(f"[MINTIFY] Backend unreachable: {e}", flush=True)
            return [{"id": "unavailable", "name": "vLLM unavailable"}]

    async def on_startup(self):
        print(f"[MINTIFY] Pipeline started. Backend: {self.valves.BACKEND_URL}", flush=True)
        try:
            resp = httpx.get(f"{self.valves.BACKEND_URL}/api/health", timeout=10)
            resp.raise_for_status()
            print("[MINTIFY] RAG backend is healthy.", flush=True)
        except Exception as e:
            print(f"[MINTIFY] WARNING: RAG backend health check failed: {e}", flush=True)

    async def on_shutdown(self):
        print("[MINTIFY] Pipeline stopped.", flush=True)

    def pipe(self, user_message: str, model_id: str, messages: list, body: dict) -> Generator:
        actual_model = model_id.removeprefix(f"{self.id}.").replace("__", "/")

        # Meta-tasks (title, tags, query gen) bypass RAG entirely.
        if _is_meta_task(user_message):
            payload = {
                "model_id": actual_model,
                "messages": messages,
                "temperature": body.get("temperature", 0.7),
            }
            _log_payload("/api/passthrough", payload)
            yield from self._stream("/api/passthrough", payload)
            return

        owui_system = next(
            (m["content"] for m in messages if m.get("role") == "system"), ""
        )
        # Strip any file content OWUI injected into the system prompt — the
        # rag-backend supplies retrieved context itself (avoids double-feeding).
        owui_system = _strip_injected_context(owui_system)
        clean_msg = _clean_user_message(user_message)
        file_ids = _extract_file_ids(body, messages)
        # 🌐 toggle → the rag-backend runs SearXNG + trafilatura over clean_msg.
        web_search = _web_search_enabled(body)
        # 💡 toggle → the rag-backend enables Qwen3.5 thinking for this turn.
        thinking = _thinking_enabled(body)
        # Per-model "Kompetenzniveau" checkbox → whether the rag-backend should
        # inject/track the competence-level profile for THIS model at all.
        kompetenz_capability = _kompetenz_capability_enabled(body)

        # Drop OWUI's <source>-injected user turn from history too — keep only the
        # clean text. Same regex on every prior user message.
        clean_messages = []
        for m in messages:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                clean_messages.append({**m, "content": _clean_user_message(m["content"])})
            else:
                clean_messages.append(m)
        # Ensure the last user message reflects the cleaned text — WITHOUT
        # destroying multimodal content. When the user attaches an image, OWUI
        # sends content as a list of parts, e.g.
        #   [{"type": "text", "text": "..."}, {"type": "image_url", ...}].
        # In that case only the text part is rewritten; the image_url part(s)
        # are preserved so vLLM still receives the image. A plain-string content
        # is replaced directly with the cleaned question.
        for m in reversed(clean_messages):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):
                    text_replaced = False
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            part["text"] = clean_msg
                            text_replaced = True
                            break
                    if not text_replaced:
                        content.insert(0, {"type": "text", "text": clean_msg})
                else:
                    m["content"] = clean_msg
                break

        payload = {
            "user_message": clean_msg,
            "model_id": actual_model,
            "messages": clean_messages,
            "temperature": body.get("temperature", 0.7),
            "owui_system_prompt": owui_system,
            "base_system_prompt": self.valves.SYSTEM_PROMPT,
            "top_k": self.valves.TOP_K,
            "file_ids": file_ids,
            "user_id": _extract_user_id(body),
            "chat_id": _extract_chat_id(body),
            "web_search": web_search,
            "thinking": thinking,
            "kompetenz_capability": kompetenz_capability,
        }

        _log_payload("/api/pipe", payload)

        yield from self._stream("/api/pipe", payload)

    def _stream(self, path: str, payload: dict) -> Generator:
        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream("POST", f"{self.valves.BACKEND_URL}{path}", json=payload) as r:
                    r.raise_for_status()
                    for chunk in r.iter_text():
                        if chunk:
                            yield chunk
        except Exception as exc:
            print(f"[MINTIFY] ERROR calling {path}: {exc}", flush=True)
            yield f"[MINTIFY Fehler] RAG-Backend nicht erreichbar: {exc}"

    def _load_valves(self) -> dict:
        path = os.path.join(os.path.dirname(__file__), "mintify_pipeline", "valves.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}
