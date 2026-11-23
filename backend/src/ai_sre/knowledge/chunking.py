"""Heading-aware chunking.

Documents are split at Markdown headings so a chunk never mixes sections, and each chunk carries
its section path ("Runbook: X > Remediation"), which is what citations point to. Long sections
are split at paragraph boundaries into chunks of at most ``max_chars`` with one paragraph of
overlap. Tiny sections are kept (a heading with two bullets is still the right citation target).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")


@dataclass(frozen=True)
class Chunk:
    index: int
    section: str
    content: str

    def embedding_text(self, title: str) -> str:
        """What gets embedded: title + section path give short chunks their context."""
        return f"{title}. {self.section}.\n{self.content}"


def _sections(body: str, title: str) -> list[tuple[str, str]]:
    path: list[str] = []
    out: list[tuple[str, str]] = []
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            out.append((" > ".join(path) or title, text))
        buf.clear()

    for line in body.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            path[:] = path[: level - 1]
            while len(path) < level - 1:
                path.append(title)
            path.append(m.group(2).strip())
        else:
            buf.append(line)
    flush()
    return out


def _split(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:  # hard-split paragraphs that alone exceed the limit
        while len(p) > max_chars:
            cut = p.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(p[:cut].strip())
            p = p[cut:].strip()
        pieces.append(p)
    chunks: list[str] = []
    current: list[str] = []
    for p in pieces:
        if current and len("\n\n".join([*current, p])) > max_chars:
            chunks.append("\n\n".join(current))
            current = [current[-1]] if len(current[-1]) < max_chars // 3 else []  # overlap
        current.append(p)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_document(title: str, body: str, max_chars: int = 900) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section, text in _sections(body, title):
        for piece in _split(text, max_chars):
            chunks.append(Chunk(index=len(chunks), section=section, content=piece))
    return chunks
