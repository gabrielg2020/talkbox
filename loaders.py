"""Read source documents into structured blocks for synthesis.

A registry maps a file extension to how its blocks are extracted and, where
extraction can be imperfect, a caveat shown to the user before they commit. Libs
are imported lazily so a missing optional dependency only bites the format that
needs it.

A "block" is a heading / paragraph / list item — the structure the read-along
view renders and that drives the pauses between spoken sections.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

# Block kinds: h1/h2/h3 headings, p paragraph, li list item.
HEADINGS = {"h1", "h2", "h3"}


@dataclass
class Block:
    kind: str
    text: str


@dataclass
class Loader:
    extract: Callable[[Path], List[Block]]
    caveat: Optional[str] = None  # warn + confirm before synth when extraction may be rough


def _para_blocks(text):
    """Split plain text into paragraph blocks on blank lines."""
    return [Block("p", chunk.strip()) for chunk in text.split("\n\n") if chunk.strip()]


def _load_txt(path):
    return _para_blocks(path.read_text())


def _load_md(path):
    import markdown
    from bs4 import BeautifulSoup

    html = markdown.markdown(path.read_text())
    soup = BeautifulSoup(html, "html.parser")

    blocks = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre"]):
        # containers (li, blockquote) are surfaced themselves; skip the <p> etc.
        # markdown nests inside them so their text isn't emitted twice
        if el.find_parent(["li", "blockquote"]):
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            kind = "h" + min(el.name[1], "3")  # cap headings at h3 for styling
        elif el.name == "li":
            kind = "li"
        else:  # p / blockquote / pre(code) read as prose, not dropped
            kind = "p"
        blocks.append(Block(kind, text))
    return blocks


def _load_docx(path):
    from docx import Document

    blocks = []
    for para in Document(str(path)).paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower()
        if style.startswith("heading"):
            level = "".join(c for c in style if c.isdigit()) or "1"
            kind = "h" + min(level, "3")
        elif "list" in style:
            kind = "li"
        else:
            kind = "p"
        blocks.append(Block(kind, text))
    return blocks


# Reliable formats carry no caveat. A future best-effort format (e.g. PDF) sets a
# one-line reason and do_generate warns automatically — no other wiring needed.
LOADERS = {
    ".txt": Loader(_load_txt),
    ".md": Loader(_load_md),
    ".docx": Loader(_load_docx),
}

SUPPORTED_GLOBS = [f"*{ext}" for ext in LOADERS]


def load_blocks(path):
    return LOADERS[path.suffix.lower()].extract(path)


def blocks_to_text(blocks):
    """Flatten blocks to plain text (for the gTTS path, which has no structure)."""
    return "\n\n".join(b.text for b in blocks)


def caveat_for(path):
    loader = LOADERS.get(path.suffix.lower())
    return loader.caveat if loader else None
