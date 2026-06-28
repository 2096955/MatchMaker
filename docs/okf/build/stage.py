"""Stage SCUDO source docs into a curated OKF source tree (build/okf-src/).

Runs under the OKF venv's interpreter so it can reuse okf_toolkit's own
document + link helpers. Never edits source docs; only writes copies into
build/okf-src/.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import yaml
from okf_toolkit.bundle.document import OKFDocument
from okf_toolkit.bundle.links import is_external, iter_markdown_links, split_target


def merge_frontmatter(raw_text: str, curated: dict) -> str:
    """Overlay *curated* keys onto any existing frontmatter; return one doc string."""
    doc = OKFDocument.parse(raw_text)
    merged = dict(doc.frontmatter)
    for key, value in curated.items():
        if value is None:
            continue
        merged[key] = value
    return OKFDocument(
        frontmatter=merged, body=doc.body, had_frontmatter=True
    ).serialize()


def _candidates(path: str) -> list[str]:
    """Raw, normalized, and ./-toggled forms so x.md and ./x.md coalesce."""
    toggled = path[2:] if path.startswith("./") else "./" + path
    seen: set[str] = set()
    out: list[str] = []
    for candidate in (path, os.path.normpath(path), toggled):
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def repoint_links(body: str, link_map: dict) -> str:
    """Rewrite whole existing markdown links; de-link @inline targets. Code-span safe."""
    edits: list[tuple[int, int, str]] = []
    for link in iter_markdown_links(body):
        path, anchor, title = split_target(link.raw_target)
        if not path or is_external(path):
            continue
        mapped = next((link_map[c] for c in _candidates(path) if c in link_map), None)
        if mapped is None:
            continue
        rb = body.rfind("]", 0, link.start)
        lb = body.rfind("[", 0, rb)
        close = body.find(")", link.end)
        if lb < 0 or rb < 0 or close < 0:
            continue
        text = body[lb + 1 : rb]
        replacement = (
            f"`{text}`" if mapped == "@inline" else f"[{text}]({mapped}{anchor}{title})"
        )
        edits.append((lb, close + 1, replacement))

    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        body = body[:start] + replacement + body[end:]
    return body


def add_related_section(body: str, related: list) -> str:
    """Append a generated '## Related' section of markdown links (no-op if empty)."""
    if not related:
        return body
    lines = ["", "## Related", ""]
    for item in related:
        lines.append(f"- [{item['label']}]({item['target']})")
    return body.rstrip() + "\n" + "\n".join(lines) + "\n"


def add_superseded_banner(body: str, target) -> str:
    """Prepend a visible supersession banner (no-op if target is falsy)."""
    if not target:
        return body
    name = target.rsplit("/", 1)[-1]
    if name.endswith(".md"):
        name = name[:-3]
    return f"> **Superseded.** See [{name}]({target}).\n\n" + body


def _merge_sources(bodies: list[str], titles: list[str]) -> str:
    parts = []
    for title, body in zip(titles, bodies):
        parts.append(f"## {title}\n\n{body.strip()}\n")
    return "\n---\n\n".join(parts)


def _split_ingestion(body: str) -> str:
    """Keep everything before the first '## Appendix' heading."""
    lines = body.splitlines()
    cut = len(lines)
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("## appendix"):
            cut = i
            break
    kept = "\n".join(lines[:cut]).rstrip()
    return kept + (
        "\n\nFor the SCUDO matching-pipeline diagrams, see "
        "[architecture diagrams & sources](/architecture/diagrams-and-sources.md).\n"
    )


def _fold_diagrams(bodies: list[str]) -> str:
    """Fold the two mapping_mcp READMEs; first source (architecture/README) leads.

    Prepend a disambiguation note: the folded text inherits phrases like "this
    directory is the source of truth", but the canonical .mmd files are NOT in this
    .md bundle — they live at their repo path. Name it so a bundle reader isn't misled.
    """
    note = (
        "> **Note:** The canonical Mermaid (`.mmd`) diagrams referred to below live in "
        "the repo at `backend/scudo_mapping_mcp/docs/architecture/`; they are not part of "
        'this `.md` bundle. Where the text below says "this directory", it means that '
        "repo path.\n\n"
    )
    return note + "\n\n---\n\n".join(b.strip() for b in bodies) + "\n"


def _read_body(repo_root: Path, src: str) -> tuple[str, str]:
    raw = (repo_root / src).read_text(encoding="utf-8")
    doc = OKFDocument.parse(raw)
    title = ""
    for ln in doc.body.splitlines():
        if ln.startswith("# "):
            title = ln[2:].strip()
            break
    return doc.body, title


def main(repo_root: str, manifest_path: str, out_dir: str) -> int:
    repo = Path(repo_root)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    manifest = yaml.safe_load((repo / manifest_path).read_text(encoding="utf-8"))
    count = 0
    for entry in manifest["concepts"]:
        bodies: list[str] = []
        titles: list[str] = []
        for src in entry["sources"]:
            body, title = _read_body(repo, src)
            bodies.append(body)
            titles.append(title)
        transform = entry.get("transform")
        if transform == "merge":
            body = _merge_sources(bodies, titles)
        elif transform == "split_ingestion":
            body = _split_ingestion(bodies[0])
        elif transform == "fold_diagrams":
            body = _fold_diagrams(bodies)
        else:
            body = bodies[0]
        body = repoint_links(body, entry.get("link_rewrites") or {})
        body = add_superseded_banner(body, entry.get("superseded_by"))
        body = add_related_section(body, entry.get("related") or [])
        curated = {
            "type": entry["type"],
            "title": entry["title"],
            "description": entry["description"],
            "tags": entry.get("tags"),
            "staleness": entry["staleness"],
            "supersedes": entry.get("supersedes"),
            "superseded_by": entry.get("superseded_by"),
        }
        first_raw = (repo / entry["sources"][0]).read_text(encoding="utf-8")
        first_doc = OKFDocument.parse(first_raw)
        merged_doc = merge_frontmatter(
            OKFDocument(
                frontmatter=first_doc.frontmatter,
                body=body,
                had_frontmatter=True,
            ).serialize(),
            curated,
        )
        dest = out / entry["out"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(merged_doc, encoding="utf-8")
        count += 1
    print(f"staged {count} concepts → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
