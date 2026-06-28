"""Unit tests for OKF staging helpers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage import (  # noqa: E402
    _fold_diagrams,
    _merge_sources,
    _split_ingestion,
    add_related_section,
    add_superseded_banner,
    merge_frontmatter,
    repoint_links,
)


def test_merge_overlays_onto_existing_frontmatter_single_block():
    raw = "---\nname: taxonomy-mapping\ndescription: old\n---\n\n# Body\n\ntext\n"
    out = merge_frontmatter(
        raw, {"type": "Skill", "description": "new", "staleness": "current"}
    )
    assert out.count("\n---\n") == 1 and out.startswith("---\n")
    assert "type: Skill" in out
    assert "description: new" in out
    assert "name: taxonomy-mapping" in out
    assert "staleness: current" in out
    assert "# Body" in out
    assert "old" not in out.split("# Body")[0]


def test_merge_creates_block_when_none_present():
    raw = "# Title\n\nbody only, no frontmatter\n"
    out = merge_frontmatter(raw, {"type": "Plan", "title": "T"})
    assert out.startswith("---\n")
    assert "type: Plan" in out
    assert "body only, no frontmatter" in out


def test_repoint_remaps_existing_link_to_bundle_absolute():
    body = "See [bands](../matching-data-provenance.md)."
    out = repoint_links(
        body,
        {"../matching-data-provenance.md": "/reference/matching-data-provenance.md"},
    )
    assert "[bands](/reference/matching-data-provenance.md)" in out


def test_repoint_inline_delinks_even_with_anchor():
    body = "the [code](batch.py#x) here"
    out = repoint_links(body, {"batch.py": "@inline"})
    assert "`code`" in out
    assert "(batch.py" not in out


def test_repoint_leaves_external_and_unmapped_untouched():
    body = "[ext](https://x.com) and [keep](./other.md)."
    out = repoint_links(body, {"missing.md": "/x.md"})
    assert "[ext](https://x.com)" in out
    assert "[keep](./other.md)" in out


def test_repoint_ignores_links_in_code_spans():
    body = "`[not a link](foo.md)` real [a](foo.md)"
    out = repoint_links(body, {"foo.md": "/bar.md"})
    assert "`[not a link](foo.md)`" in out
    assert "[a](/bar.md)" in out


def test_repoint_normalizes_dot_slash():
    body = "[a](x.md) [b](./x.md)"
    out = repoint_links(body, {"x.md": "/y.md"})
    assert out.count("(/y.md)") == 2


def test_add_related_section_injects_links():
    out = add_related_section(
        "# Doc\n\nbody\n",
        [
            {
                "label": "Bands (canonical)",
                "target": "/reference/matching-data-provenance.md",
            }
        ],
    )
    assert "## Related" in out
    assert "[Bands (canonical)](/reference/matching-data-provenance.md)" in out


def test_add_related_section_noop_when_empty():
    assert add_related_section("body\n", []) == "body\n"


def test_add_superseded_banner_prepends():
    out = add_superseded_banner(
        "# Old\n\nbody\n", "/architecture/diagrams-and-sources.md"
    )
    assert out.startswith("> **Superseded.**")
    assert "(/architecture/diagrams-and-sources.md)" in out


def test_add_superseded_banner_noop_when_none():
    assert add_superseded_banner("body\n", None) == "body\n"


def test_merge_sources_concatenates_with_headers():
    out = _merge_sources(["body A", "body B"], ["Apply Bundle", "Repo Push"])
    assert "body A" in out and "body B" in out
    assert "Apply Bundle" in out and "Repo Push" in out
    assert out.index("body A") < out.index("body B")


def test_split_ingestion_drops_appendices():
    body = "## 1. Scope\nkeep me\n## 11. Done\nkeep\n## Appendix A\nDROP DIAGRAM\n"
    out = _split_ingestion(body)
    assert "keep me" in out
    assert "DROP DIAGRAM" not in out
    assert "Appendix A" not in out


def test_fold_diagrams_keeps_both_unique_sections():
    out = _fold_diagrams(["SUPERSEDES mapping here", "Quick orientation bullets"])
    assert "SUPERSEDES mapping here" in out
    assert "Quick orientation bullets" in out
