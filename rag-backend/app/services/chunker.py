"""
Chunker — structure-aware splitting of extracted document text
---------------------------------------------------------------
Replaces the old "cut every N characters" splitter, which happily sliced
Markdown tables in half, orphaned rows from their header row and started
chunks mid-sentence. Retrieved passages are shown to the user in OWUI
(citation list) and fed to the LLM verbatim, so a chunk has to read as a
self-contained piece of the document.

The input is whatever OWUI / docling extracted:
  - docling Markdown for PDFs, Office docs, scans (## headings, | tables |,
    - lists, <!-- image --> placeholders)
  - plain text for text-like files

Three ideas, nothing more:

  1. PARSE the text into structural blocks (heading / table / list / code /
     paragraph). A block is atomic — it is never cut unless it alone is
     bigger than `max_size`.
  2. PACK whole blocks greedily into chunks of ~`size` chars. A new chunk is
     also started at a heading, as long as the current one isn't still tiny
     (`min_size`) — so sections stay together instead of bleeding into
     each other.
  3. PREFIX every chunk with the Markdown headings it lives under, so a chunk
     torn out of the middle of a document still says what it is about
     ("## Gesetzliche Abzüge"). Continuation chunks get "(Fortsetzung)".

Only oversized blocks are split, and each kind is split where it is safe:
  - table     → at row boundaries, repeating the header + divider row in
                every part (a half table still has its column names)
  - list      → at item boundaries
  - paragraph → at sentence boundaries (German abbreviations respected)
  - anything  → at word boundaries as a last resort

Overlap is carried over as whole trailing blocks (never a table), not as a
blind character window.
"""

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*#*\s*$")
_TABLE_LINE_RE = re.compile(r"^\s*\|")
_TABLE_DIVIDER_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+•‣]|\d{1,3}[.)])\s+")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
# docling emits these for images when image_export_mode=placeholder — pure noise
# in a retrieved passage.
_NOISE_RE = re.compile(r"^\s*<!--\s*image\s*-->\s*$", re.IGNORECASE)
# Sentence end: .!? followed by whitespace and something that starts a sentence.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'„»(]?[A-ZÄÖÜ0-9])")
# Words that end in a period without ending a sentence (plus any single letter,
# which covers initials and the "z. B." / "u. a." split forms).
_ABBREVIATIONS = {
    "abs", "art", "bspw", "bzw", "ca", "evtl", "ggf", "inkl", "max", "min",
    "mio", "mrd", "nr", "tsd", "usw", "vgl", "z", "zzgl",
}
_ABBREV_TAIL_RE = re.compile(r"(\w+)\.\s*$")

# At most this many heading levels are prefixed to a chunk — a deep outline
# would otherwise spend the whole chunk on breadcrumbs.
_MAX_PATH_LEVELS = 3


@dataclass
class _Block:
    kind: str          # "heading" | "table" | "list" | "code" | "para"
    text: str
    level: int = 0     # heading level (1–6), 0 for everything else


def chunk_markdown(
    text: str,
    size: int = 800,
    overlap: int = 100,
    max_size: int | None = None,
    min_size: int | None = None,
) -> list[str]:
    """Split extracted document text into readable, self-describing chunks.

    size      — target chunk length in characters
    overlap   — how many characters of trailing context may be repeated in the
                next chunk (whole blocks only, never a table)
    max_size  — hard cap; a single block larger than `size` but ≤ this stays
                intact (default: 2 × size), a larger one is split by kind
    min_size  — a chunk shorter than this is not closed at a heading; the next
                section is appended instead (default: size // 4)
    """
    max_size = max_size or size * 2
    min_size = min_size if min_size is not None else max(150, size // 4)

    blocks = _split_oversized(_parse_blocks(text), size, max_size)
    if not blocks:
        return []

    chunks: list[str] = []
    stack: list[_Block] = []      # open headings, outermost first
    cur: list[_Block] = []        # blocks of the chunk being built
    cur_len = 0
    cur_path: list[str] = []      # heading lines prefixed to the current chunk
    continuation = False          # current chunk continues the previous section

    def path_now() -> list[str]:
        return [h.text for h in stack[-_MAX_PATH_LEVELS:]]

    def flush(carry: bool) -> None:
        """Close the current chunk. `carry` keeps trailing blocks as overlap."""
        nonlocal cur, cur_len, cur_path, continuation
        if not cur or not any(b.kind != "heading" for b in cur):
            # Nothing but headings — let them ride along with the next chunk
            # instead of becoming a content-free passage.
            return
        chunks.append(_render(cur_path, cur, continuation))

        tail: list[_Block] = []
        if carry and overlap > 0:
            total = 0
            for b in reversed(cur):
                # Tables/code/headings are never repeated: a stray header row or
                # duplicated heading reads as broken, not as context.
                if b.kind in ("table", "code", "heading"):
                    break
                if total + len(b.text) > overlap:
                    break
                tail.insert(0, b)
                total += len(b.text)
        cur = tail
        cur_len = sum(len(b.text) for b in tail)
        continuation = carry
        if tail:
            cur_path = path_now()

    for block in blocks:
        if block.kind == "heading":
            if cur_len >= min_size:
                flush(carry=False)
            while stack and stack[-1].level >= block.level:
                stack.pop()
            if not cur:
                # The heading itself opens this chunk, so only its ancestors go
                # into the prefix — it would otherwise appear twice.
                cur_path = path_now()
                continuation = False
            stack.append(block)
            cur.append(block)
            cur_len += len(block.text)
            continue

        if cur and cur_len + len(block.text) > size:
            flush(carry=True)
        if not cur:
            # `continuation` is deliberately left as flush() set it: a chunk that
            # opens with ordinary text after a size split is still the same
            # section, and says so in its prefix.
            cur_path = path_now()
        cur.append(block)
        cur_len += len(block.text)

    flush(carry=False)
    if cur and chunks:
        # Headings at the very end of a document with nothing under them (a
        # section that was only an image). flush() refuses to emit a
        # content-free chunk, so they ride along with the previous one instead
        # of disappearing from the index.
        chunks[-1] = chunks[-1] + "\n\n" + "\n\n".join(b.text for b in cur)
    elif cur:
        chunks.append(_render(cur_path, cur, continuation))
    return [c for c in chunks if c.strip()]


# --------------------------------------------------------------------- render


def _render(path: list[str], blocks: list[_Block], continuation: bool) -> str:
    head = list(path)
    if head and continuation:
        head[-1] = f"{head[-1]} (Fortsetzung)"
    body = "\n\n".join(b.text for b in blocks).strip()
    if not head:
        return body
    return "\n\n".join(head) + "\n\n" + body


# ---------------------------------------------------------------------- parse


def _parse_blocks(text: str) -> list[_Block]:
    """Group lines into atomic structural blocks."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[_Block] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        if not line.strip() or _NOISE_RE.match(line):
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(_Block("heading", line.strip(), len(heading.group(1))))
            i += 1
            continue

        if _FENCE_RE.match(line):
            buf = [line.rstrip()]
            i += 1
            while i < n:
                buf.append(lines[i].rstrip())
                closing = _FENCE_RE.match(lines[i])
                i += 1
                if closing:
                    break
            blocks.append(_Block("code", "\n".join(buf)))
            continue

        if _TABLE_LINE_RE.match(line):
            buf = []
            while i < n and _TABLE_LINE_RE.match(lines[i]):
                buf.append(lines[i].rstrip())
                i += 1
            blocks.append(_Block("table", "\n".join(buf)))
            continue

        # List or paragraph: run until a blank line or a block of another kind.
        kind = "list" if _LIST_RE.match(line) else "para"
        buf = []
        while i < n and lines[i].strip():
            nxt = lines[i]
            if _HEADING_RE.match(nxt) or _TABLE_LINE_RE.match(nxt) or _FENCE_RE.match(nxt):
                break
            if kind == "para" and _LIST_RE.match(nxt) and buf:
                break
            if not _NOISE_RE.match(nxt):
                buf.append(nxt.rstrip())
            i += 1
        if buf:
            blocks.append(_Block(kind, "\n".join(buf)))

    return blocks


# ----------------------------------------------------------- oversized blocks


def _split_oversized(blocks: list[_Block], size: int, max_size: int) -> list[_Block]:
    out: list[_Block] = []
    for b in blocks:
        if len(b.text) <= max_size:
            out.append(b)
        else:
            out.extend(_Block(b.kind, t) for t in _split_block(b, size, max_size))
    return out


def _split_block(b: _Block, size: int, max_size: int) -> list[str]:
    """Split one oversized block where its kind allows it."""
    if b.kind == "table":
        # Rows are never broken up — a table cut mid-row is unreadable, and a
        # row longer than the cap is pathological anyway.
        return _split_table(b.text, size)
    if b.kind == "list":
        parts = _pack_lines(b.text.split("\n"), size)
    elif b.kind == "para":
        parts = _split_paragraph(b.text, size, max_size)
    else:
        parts = _split_words(b.text, size)
    # A single over-long line (a "list item" that is really a whole paragraph,
    # a wall of text without blank lines) still has to come down to the cap.
    out: list[str] = []
    for part in parts:
        if len(part) <= max_size:
            out.append(part)
        else:
            out.extend(_split_paragraph(part, size, max_size))
    return out


def _split_table(text: str, size: int) -> list[str]:
    """Split a table at row boundaries, repeating the header in every part."""
    rows = text.split("\n")
    if len(rows) >= 2 and _TABLE_DIVIDER_RE.match(rows[1]):
        header, body = rows[:2], rows[2:]
    else:
        header, body = [], rows

    header_len = sum(len(r) + 1 for r in header)
    parts: list[str] = []
    cur: list[str] = []
    cur_len = header_len
    for row in body:
        if cur and cur_len + len(row) + 1 > size:
            parts.append("\n".join(header + cur))
            cur, cur_len = [], header_len
        cur.append(row)
        cur_len += len(row) + 1
    if cur:
        parts.append("\n".join(header + cur))
    return parts or [text]


def _split_paragraph(text: str, size: int, max_size: int) -> list[str]:
    pieces: list[str] = []
    for sentence in _split_sentences(text):
        if len(sentence) > max_size:
            pieces.extend(_split_words(sentence, size))
        else:
            pieces.append(sentence)
    return _pack_lines(pieces, size, joiner=" ")


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(text)
    out: list[str] = []
    for part in parts:
        if out and _ends_with_abbreviation(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [p for p in out if p.strip()]


def _ends_with_abbreviation(text: str) -> bool:
    m = _ABBREV_TAIL_RE.search(text)
    if not m:
        return False
    word = m.group(1)
    return len(word) == 1 or word.lower() in _ABBREVIATIONS


def _split_words(text: str, size: int) -> list[str]:
    """Last resort for a single block/sentence longer than the hard cap."""
    return _pack_lines(text.split(" "), size, joiner=" ")


def _pack_lines(items: list[str], size: int, joiner: str = "\n") -> list[str]:
    """Greedily group items into pieces of at most `size` chars."""
    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for item in items:
        add = len(item) + len(joiner)
        if cur and cur_len + add > size:
            parts.append(joiner.join(cur))
            cur, cur_len = [], 0
        cur.append(item)
        cur_len += add
    if cur:
        parts.append(joiner.join(cur))
    return parts
