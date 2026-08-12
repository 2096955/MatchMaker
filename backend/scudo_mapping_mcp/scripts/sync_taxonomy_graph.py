"""Sync the canonical taxonomy graph analyzer into the standalone JPMC tree."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CANONICAL_MODULE = ROOT / "backend" / "scudo_mapping_mcp" / "taxonomy_graph.py"
CANONICAL_MODELS = ROOT / "backend" / "scudo_mapping_mcp" / "taxonomy_graph_models.py"
JPMC_MODULE = ROOT / "jpmc-port" / "scudo" / "taxonomy_graph.py"
JPMC_MODELS = ROOT / "jpmc-port" / "scudo" / "taxonomy_graph_models.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync() -> None:
    shutil.copyfile(CANONICAL_MODULE, JPMC_MODULE)
    shutil.copyfile(CANONICAL_MODELS, JPMC_MODELS)


if __name__ == "__main__":
    sync()
    print(f"taxonomy_graph.py sha256={digest(JPMC_MODULE)}")
    print(f"taxonomy_graph_models.py sha256={digest(JPMC_MODELS)}")
