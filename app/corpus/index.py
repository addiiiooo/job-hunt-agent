"""BM25 retrieval over the owner's own corpus.

Pure Python, in process, rebuilt at startup. There is no vector database
because there is no corpus large enough to need one: a resume and a handful of
project write-ups is a few dozen chunks, and BM25 over that is exact, instant
and explainable. The cost is paraphrase — see "Known limitations" in the README.

Granularity is the markdown section, not the file. A citation that says
``resume#databases`` points at a paragraph a reader can check; one that says
``resume`` points at a whole resume and proves nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.models import Evidence

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+.#_-]*")

# Deliberately tiny. An aggressive stoplist on a corpus this small removes
# signal, not noise.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "as", "at", "by", "it", "this",
    "that", "from", "into", "than", "then", "so", "such",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str      # "resume#databases"
    doc_id: str        # "resume"
    doc_title: str
    heading: str
    text: str
    path: str


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "section"


def parse_document(path: Path) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8")
    doc_id, title = path.stem, path.stem

    m = _FRONT_MATTER.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip().strip("\"'")
            if key == "id" and value:
                doc_id = value
            elif key == "title" and value:
                title = value
        raw = raw[m.end():]

    raw = _HTML_COMMENT.sub("", raw)

    chunks: list[Chunk] = []
    heading = "overview"
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}#{_slug(heading)}",
                    doc_id=doc_id,
                    doc_title=title,
                    heading=heading,
                    text=body,
                    path=str(path),
                )
            )

    for line in raw.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip()
            buf = []
        else:
            buf.append(line)
    flush()
    return chunks


class CorpusIndex:
    """Built once at startup. ``size()`` is what /health reports."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._bm25 = BM25Okapi([tokenize(c.text + " " + c.heading) for c in chunks]) if chunks else None

    @classmethod
    def from_directory(cls, directory: str | Path) -> CorpusIndex:
        d = Path(directory)
        chunks: list[Chunk] = []
        if d.is_dir():
            for path in sorted(d.glob("*.md")):
                chunks.extend(parse_document(path))
        return cls(chunks)

    # -- introspection -------------------------------------------------------

    def size(self) -> int:
        """Number of distinct source documents."""
        return len({c.doc_id for c in self.chunks})

    def chunk_count(self) -> int:
        return len(self.chunks)

    def chunk_ids(self) -> set[str]:
        return {c.chunk_id for c in self.chunks}

    def is_empty(self) -> bool:
        return not self.chunks

    # -- retrieval -----------------------------------------------------------

    def query(self, text: str, k: int = 5) -> list[Evidence]:
        if self._bm25 is None or not text.strip():
            return []
        scores = self._bm25.get_scores(tokenize(text))
        ranked = sorted(zip(self.chunks, scores, strict=False), key=lambda p: p[1], reverse=True)
        out: list[Evidence] = []
        for chunk, score in ranked[: max(0, k)]:
            if score <= 0:
                continue  # a zero score is "no lexical overlap", not weak evidence
            out.append(
                Evidence(
                    doc_id=chunk.chunk_id,
                    snippet=_snippet(chunk),
                    score=round(float(score), 4),
                )
            )
        return out


def _snippet(chunk: Chunk, limit: int = 320) -> str:
    body = " ".join(chunk.text.split())
    if len(body) > limit:
        body = body[:limit].rsplit(" ", 1)[0] + "…"
    return f"{chunk.doc_title} › {chunk.heading} — {body}"
