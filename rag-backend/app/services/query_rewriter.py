"""
QueryRewriter — turn chat messages into document-vocabulary search queries
---------------------------------------------------------------------------
Retrieval embeds ONLY the latest user message, which fails in two ways:

1. Follow-ups ("Ich meine den HSN/TSN", "Und die Schlüsselnummer?") carry none
   of the conversation's topic.
2. Users ask in everyday words while documents use form/contract vocabulary —
   "Wie viel kostet das Auto?" never lexically matches "Kaufpreis", "FIN"
   never matches "Fahrgestellnr.". The BM25 arm needs the document's terms in
   the query (verified empirically: expansion moves the right chunks from
   rank ≥8 into the top 5).

Before retrieval, this service asks the chat LLM (one short, non-streaming
call to the model already loaded in vLLM) to resolve references against the
recent history AND expand the question with synonyms, technical terms, and
typical German document abbreviations.

ANY failure falls back to the raw user message — retrieval must never break
because of the rewriter.
"""

import re

from app.services.dates import today_german
from app.services.llm_client import LLMClient

_REWRITE_PROMPT = """\
Du hilfst einem Dokumenten-Suchsystem (Verträge, Rechnungen, Finanzdokumente). \
Formuliere die LETZTE Nutzernachricht in EINE eigenständige deutsche Suchanfrage um:
1. Löse Pronomen und Verweise ("das", "es", "ich meine ...") mithilfe des \
Gesprächsverlaufs auf.
2. Erweitere die Anfrage um Synonyme, Fachbegriffe und übliche Abkürzungen, wie \
sie wörtlich in deutschen Dokumenten und Formularen stehen (z. B. "was kostet" → \
"Kaufpreis Preis Gesamtbetrag"; "FIN" → "FIN Fahrgestellnummer Fahrgestellnr"; \
"Schlüsselnummer" → "HSN TSN Schlüsselnummer").
3. Nenne NUR unterscheidungskräftige Begriffe. Lass allgemeine Wörter weg, die \
in fast jedem Dokument vorkommen (z. B. "Auto", "Vertrag", "Dokument", "Nummer") \
— sie verwässern die Suche.

Gesprächsverlauf:
{history}

Letzte Nutzernachricht: {question}

Antworte NUR mit der Suchanfrage — eine Zeile, kein Erklärtext, keine \
Anführungszeichen."""

_WEB_REWRITE_PROMPT = """\
Du hilfst einer Internet-Suchmaschine. Formuliere die LETZTE Nutzernachricht in \
EINE eigenständige deutsche Websuchanfrage um:
1. Löse Pronomen und Verweise ("das Spiel", "es", "dort") mithilfe des \
Gesprächsverlaufs auf — nenne die konkreten Namen, Orte und Ereignisse.
2. Heute ist {today}. Ersetze relative Zeitangaben ("gestern", "letzte Woche") \
durch das konkrete Datum oder den Namen des Ereignisses aus dem Gesprächsverlauf.
3. Formuliere kurz und stichwortartig wie eine gute Suchmaschinenanfrage — \
keine Füllwörter, keine ganzen Sätze.

Gesprächsverlauf:
{history}

Letzte Nutzernachricht: {question}

Antworte in GENAU diesem Format (zwei Zeilen, kein weiterer Text):
SUCHE: <die Suchanfrage>
AKTUELL: ja oder nein — "ja" wenn die Antwort von aktuellen Ereignissen \
abhängt (Ergebnisse, Nachrichten, Preise, Termine), sonst "nein"."""

# Number of trailing chat messages given to the rewriter and per-message cap —
# enough to resolve references without paying for the whole conversation.
_MAX_HISTORY_MESSAGES = 6
_MAX_MESSAGE_CHARS = 500


class QueryRewriter:
    def __init__(self, llm: LLMClient, enabled: bool = True):
        self._llm = llm
        self._enabled = enabled

    def rewrite(self, user_message: str, messages: list[dict], model_id: str) -> str:
        """Return an expanded standalone search query for user_message, or
        user_message itself when rewriting is disabled or the call fails."""
        if not self._enabled:
            return user_message

        # Also runs on the FIRST message: there is nothing to resolve yet, but
        # the vocabulary expansion (everyday words → document terms) is what
        # makes the BM25 arm land on the right chunks.
        history = self._history_lines(user_message, messages)

        try:
            raw = self._llm.complete(
                messages=[
                    {
                        "role": "user",
                        "content": _REWRITE_PROMPT.format(
                            history="\n".join(history) or "(keine — erste Nachricht)",
                            question=user_message,
                        ),
                    }
                ],
                model=model_id,
                temperature=0.0,
                # Generous cap: thinking models may spend tokens on a <think>
                # block before the actual one-line query.
                max_tokens=400,
            )
            rewritten = self._clean(raw)
        except Exception as e:
            print(f"[RAG] Query rewrite failed ({e}) — using the raw message.")
            return user_message

        if not rewritten:
            print("[RAG] Query rewrite returned nothing usable — using the raw message.")
            return user_message
        print(f"[RAG] rewrite: {user_message!r} -> {rewritten!r}")
        return rewritten

    def rewrite_web(
        self, user_message: str, messages: list[dict], model_id: str
    ) -> tuple[str, bool]:
        """Return (standalone web search query, is_time_sensitive) for
        user_message. Falls back to (user_message, False) whenever rewriting
        is disabled or anything fails — web search must never break here."""
        if not self._enabled:
            return user_message, False

        history = self._history_lines(user_message, messages)

        try:
            raw = self._llm.complete(
                messages=[
                    {
                        "role": "user",
                        "content": _WEB_REWRITE_PROMPT.format(
                            today=today_german(),
                            history="\n".join(history) or "(keine — erste Nachricht)",
                            question=user_message,
                        ),
                    }
                ],
                model=model_id,
                temperature=0.0,
                max_tokens=400,
            )
        except Exception as e:
            print(f"[RAG] Web query rewrite failed ({e}) — using the raw message.")
            return user_message, False

        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        if "<think>" in text:  # ran out of tokens mid-thought
            return user_message, False
        q = re.search(r"SUCHE:\s*(.+)", text)
        query = q.group(1).strip().strip("\"'„“ ") if q else ""
        if not query:
            print("[RAG] Web query rewrite returned nothing usable — using the raw message.")
            return user_message, False
        a = re.search(r"AKTUELL:\s*(ja|nein)", text, flags=re.IGNORECASE)
        recent = bool(a and a.group(1).lower() == "ja")
        print(f"[RAG] web rewrite: {user_message!r} -> {query!r} (aktuell={recent})")
        return query, recent

    @staticmethod
    def _history_lines(user_message: str, messages: list[dict]) -> list[str]:
        """The last few user/assistant turns BEFORE the current message, as
        'Nutzer:'/'Assistent:' lines. Empty when there is no prior exchange."""
        prior = [
            m
            for m in messages
            if m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]
        # The current question is normally the last user entry — don't repeat it.
        if prior and prior[-1].get("role") == "user" and prior[-1].get("content") == user_message:
            prior = prior[:-1]
        prior = prior[-_MAX_HISTORY_MESSAGES:]
        label = {"user": "Nutzer", "assistant": "Assistent"}
        return [
            f"{label[m['role']]}: {m['content'][:_MAX_MESSAGE_CHARS]}" for m in prior
        ]

    @staticmethod
    def _clean(raw: str) -> str:
        """Strip <think> blocks (Qwen3.5), quotes and whitespace; keep one line."""
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        # An unterminated <think> means the token budget ran out mid-thought —
        # nothing usable follows.
        if "<think>" in text:
            return ""
        # If the model still returned several lines, the query is the last
        # non-empty one (preambles come first).
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        if not lines:
            return ""
        return lines[-1].strip('"\'„“ ')
