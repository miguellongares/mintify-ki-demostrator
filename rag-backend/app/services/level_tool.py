"""
level_tool — the "Kompetenzniveau" tool the model can call from inside its answer
---------------------------------------------------------------------------------
vLLM is launched without --enable-auto-tool-choice / --tool-call-parser (see
scripts/init_models.sh), and the pipeline<->backend transport is plain text, not
SSE — so `delta.tool_calls` is not available to us. Instead the tool is expressed
as a contract in the system prompt: the model writes a marker line

    [[KOMPETENZNIVEAU:<0-3>|<kurze Begründung>]]

and this module (a) supplies the German prompt blocks that declare the tool and
the learner's current level, and (b) filters that marker back out of the token
stream before the user ever sees it, invoking a callback that persists the level.

Both prompt blocks are appended to the system prompt AFTER routers/rag.py has run
.format(context=...) over it — same discipline as WEB_CONTEXT_BLOCK — so braces in
any of this text can never break str.format().
"""

import re
from typing import Callable, Generator, Iterable

LEVEL_LABELS = {
    0: "kein bzw. kaum finanzielles Vorwissen",
    1: "Grundlagen vorhanden",
    2: "fortgeschrittenes Verständnis",
    3: "Expertenwissen",
}


def label(level: int) -> str:
    return LEVEL_LABELS.get(level, "unbekannt")


_PROFILE_KNOWN = (
    "\n\n[LERNENDENPROFIL]\n"
    "Kompetenzniveau der lernenden Person: {level} von 3 ({label}).\n"
    "Begründung der letzten Einstufung: {reason}\n"
    "Zuletzt aktualisiert: {updated}\n"
    "Passe Wortwahl, Erklärtiefe, Fachbegriffe und Beispiele an dieses Niveau an.\n"
    "Führe KEINE erneute Kompetenzdiagnose durch, solange du keinen konkreten "
    "Anlass dazu hast.\n"
    "[/LERNENDENPROFIL]\n"
)

_PROFILE_UNKNOWN = (
    "\n\n[LERNENDENPROFIL]\n"
    "Kompetenzniveau der lernenden Person: noch nicht ermittelt.\n"
    "Wenn es für deine Aufgabe relevant ist, ermittle es mit einer kurzen "
    "Kompetenzdiagnose und speichere es anschließend mit dem unten beschriebenen "
    "Werkzeug.\n"
    "[/LERNENDENPROFIL]\n"
)

TOOL_CONTRACT = (
    "\n\n[WERKZEUG: Kompetenzniveau speichern]\n"
    "Du kannst das Kompetenzniveau der lernenden Person dauerhaft speichern.\n"
    "Schreibe dazu — und NUR dann, wenn du das Niveau tatsächlich ermittelt oder "
    "begründet geändert hast — genau einmal am Ende deiner Antwort die Zeile:\n"
    "[[KOMPETENZNIVEAU:<Zahl 0-3>|<kurze Begründung>]]\n"
    "Skala: 0 = kein Vorwissen, 1 = Grundlagen, 2 = fortgeschritten, 3 = Experte.\n"
    "Die Begründung ist ein kurzer Satz ohne eckige Klammern.\n"
    "Diese Zeile wird automatisch verarbeitet und der Person NICHT angezeigt. "
    "Erkläre der Person das Ergebnis deshalb zusätzlich in normalem Fließtext.\n"
    "[/WERKZEUG]\n"
)


def profile_block(record: dict | None) -> str:
    """The [LERNENDENPROFIL] block for a stored level record (or None)."""
    if not record:
        return _PROFILE_UNKNOWN
    level = record.get("level")
    return _PROFILE_KNOWN.format(
        level=level,
        label=label(level),
        reason=record.get("reason") or "keine angegeben",
        updated=(record.get("updated_at") or "unbekannt")[:10],
    )


# --- marker interception -----------------------------------------------------

_MARKER_RE = re.compile(r"\[\[KOMPETENZNIVEAU\s*:\s*([0-3])\s*(?:\|([^\]]*))?\]\]")
_PREFIX = "[[KOMPETENZNIVEAU"
# A "[[" that never grows into a closed marker must not stall the stream forever.
_MAX_HOLD = 400


def _hold_index(buf: str) -> int:
    """Leftmost index from which `buf` could still be growing into a marker.

    Everything before it is safe to emit; everything from it on must be held back,
    because vLLM streams token by token and a marker arrives split across many
    chunks ("[[KOMP" / "ETENZNIVEAU" / ":2|..." / "]]"). Without this the marker
    would leak into the browser character by character.
    """
    i = buf.find("[")
    while i != -1:
        tail = buf[i:]
        # Either we are already inside the marker, or the tail so far is still a
        # possible prefix of it.
        if tail.startswith(_PREFIX) or _PREFIX.startswith(tail):
            return i
        i = buf.find("[", i + 1)
    return len(buf)


def _consume(
    buf: str, on_level: Callable[[int, str], None], final: bool
) -> tuple[str, str]:
    """Pull complete markers out of `buf`. Returns (text_to_emit, remainder)."""
    out: list[str] = []
    while True:
        match = _MARKER_RE.search(buf)
        if not match:
            break
        out.append(buf[: match.start()])
        level = int(match.group(1))
        reason = (match.group(2) or "").strip()
        try:
            on_level(level, reason)
        except Exception as exc:
            # A failed write must never break the user's answer.
            print(f"[Kompetenz] could not store level {level}: {exc}", flush=True)
        buf = buf[match.end() :]

    if final:
        return "".join(out) + buf, ""

    hold = _hold_index(buf)
    if len(buf) - hold > _MAX_HOLD:
        # Held far too long without a closing "]]" — it was never a marker.
        return "".join(out) + buf, ""
    out.append(buf[:hold])
    return "".join(out), buf[hold:]


def filter_stream(
    tokens: Iterable[str], on_level: Callable[[int, str], None]
) -> Generator[str, None, None]:
    """Yield the model's tokens with every [[KOMPETENZNIVEAU:...]] marker removed.

    `on_level(level, reason)` fires for each complete marker. If the model emits
    more than one (e.g. it corrects itself mid-answer), each fires and the store's
    UPSERT makes the last one win — which is the intended semantics.
    """
    buf = ""
    for chunk in tokens:
        if not chunk:
            continue
        emit, buf = _consume(buf + chunk, on_level, final=False)
        if emit:
            yield emit
    emit, buf = _consume(buf, on_level, final=True)
    if emit:
        yield emit
