#!/usr/bin/env python3
"""Live LLM smoke — real Anthropic Messages API calls (no Deterministic stubs).

Default target is Opus 4.8. For local/Cursor testing when Opus is unavailable,
pass ``--model cursor`` (uses ``ANTHROPIC_MODEL`` / Claude Code default via the
shim) or any explicit model id.

Usage:
  unset SCUDO_LOCAL
  export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
  export ANTHROPIC_API_KEY="$(tr -d '[:space:]' < ~/.codex/shim-router/router.key)"

  # Opus 4.8 (ARB evidence)
  PYTHONPATH=. python run_opus_smoke.py --out /tmp/scudo-opus-smoke.json

  # Cursor / Claude Code default model (dev testing fallback)
  PYTHONPATH=. python run_opus_smoke.py --model cursor --out /tmp/scudo-cursor-smoke.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_OPUS_IDS = ("claude-opus-4-8", "anthropic.claude-opus-4-8", "opus-4-8")
_CURSOR_FALLBACK = "claude-sonnet-5"


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live LLM port smoke (Opus or Cursor model)"
    )
    p.add_argument("--out", type=Path, default=Path("/tmp/scudo-opus-smoke.json"))
    p.add_argument(
        "--case-id",
        default="lseg-ibes-equity-research",
        help="Golden case_id to run (default: one positive equity case)",
    )
    p.add_argument(
        "--model",
        default="opus",
        help=(
            "opus | cursor | <explicit model id>. "
            "'cursor' uses ANTHROPIC_MODEL (Claude Code / Cursor shim default)."
        ),
    )
    p.add_argument(
        "--auto-fallback",
        action="store_true",
        help="If Opus probe fails, fall back to Cursor/shim default model",
    )
    return p.parse_args()


def _ensure_shim_env() -> None:
    os.environ.pop("SCUDO_LOCAL", None)
    os.environ["SCUDO_AGENT_MODE"] = "anthropic"
    os.environ.setdefault("ANTHROPIC_BASE_URL", "http://127.0.0.1:8787")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        key_path = Path.home() / ".codex" / "shim-router" / "router.key"
        if key_path.is_file():
            os.environ["ANTHROPIC_API_KEY"] = key_path.read_text(
                encoding="utf-8"
            ).strip()


def _normalize_model_id(raw: str) -> str:
    mid = (raw or "").strip()
    if mid.startswith("us.anthropic."):
        mid = mid.removeprefix("us.anthropic.")
    if mid.startswith("anthropic."):
        mid = mid.removeprefix("anthropic.")
    return mid or _CURSOR_FALLBACK


def _resolve_model(choice: str) -> tuple[str, str]:
    """Return (model_id, provenance_label)."""
    c = (choice or "opus").strip().lower()
    if c in {"opus", "opus48", "opus-4-8", "claude-opus-4-8"}:
        return "claude-opus-4-8", "opus-4-8"
    if c in {"cursor", "cursor-default", "dev", "shim-default"}:
        raw = (
            os.environ.get("ANTHROPIC_MODEL")
            or os.environ.get("ANTHROPIC_SMALL_FAST_MODEL")
            or _CURSOR_FALLBACK
        )
        return _normalize_model_id(raw), "cursor-shim-default"
    return _normalize_model_id(choice), "explicit"


def _probe_model(model_id: str) -> tuple[bool, str, str | None]:
    """Cheap live probe — returns (ok, detail, echoed_model)."""
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not base or not key:
        return False, "missing ANTHROPIC_BASE_URL or ANTHROPIC_API_KEY", None
    body = json.dumps(
        {
            "model": model_id,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return False, f"HTTP {exc.code}: {detail}", None
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return False, f"{type(exc).__name__}: {exc}", None
    text = ""
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text") or ""
    got_model = payload.get("model")
    return True, f"model={got_model or model_id} text={text!r}", got_model


def main() -> int:
    args = _parse()
    sys.path.insert(0, str(ROOT))
    _ensure_shim_env()

    model_id, provenance = _resolve_model(args.model)
    echoed_model: str | None = None
    probe_detail = ""
    # Always probe so the report can record server-echoed model identity.
    ok, probe_detail, echoed_model = _probe_model(model_id)
    print(f"Model probe: ok={ok} ({probe_detail})", flush=True)
    if args.auto_fallback and provenance == "opus-4-8" and not ok:
        model_id, provenance = _resolve_model("cursor")
        print(
            f"FALLBACK → Cursor/shim model={model_id} ({provenance})",
            flush=True,
        )
        ok, probe_detail, echoed_model = _probe_model(model_id)
        print(f"Fallback probe: ok={ok} ({probe_detail})", flush=True)

    os.environ["SCUDO_ANTHROPIC_MODEL_ID"] = model_id

    from scudo.ab_compare import load_ab_cases, normalize_prediction, run_port_arm
    from scudo.shared.bedrock import anthropic_llm_id

    # Drop any prior cached agents so model id change takes effect.
    from scudo import agents as agents_mod

    agents_mod._AGENTS_CACHE.clear()

    cases = [
        c
        for c in load_ab_cases(ROOT / "fixtures" / "ab_golden.jsonl")
        if c["case_id"] == args.case_id
    ]
    if not cases:
        raise SystemExit(f"case_id not found: {args.case_id}")

    requested = anthropic_llm_id()
    print(
        f"LIVE smoke model_requested={requested} echoed={echoed_model} "
        f"provenance={provenance} base={os.environ.get('ANTHROPIC_BASE_URL')} "
        f"case={args.case_id}",
        flush=True,
    )
    rows = run_port_arm(cases, mode="anthropic")
    row = rows[0]
    pred = row["prediction"]
    pins = (row.get("mapping_object") or {}).get("invocation_pins") or {}
    # Outcomes that cannot come from DeterministicMappingAgent (0.92/20).
    stub_scores = pred.get("confidence") == 0.92 and pred.get("verifier_total") == 20
    report = {
        "mode": "anthropic",
        "model_requested": requested,
        "model": requested,  # legacy field = requested id (not server echo)
        "echoed_model": echoed_model,
        "model_provenance": provenance,
        "is_opus_requested": requested in _OPUS_IDS or requested.endswith("opus-4-8"),
        "echoed_matches_request": (
            echoed_model is not None
            and (
                echoed_model == requested
                or echoed_model.endswith(requested)
                or requested.endswith(echoed_model)
            )
        ),
        "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
        "probe_ok": ok,
        "probe_detail": probe_detail,
        "case_id": args.case_id,
        "prediction": pred,
        "mapping_loop_turns": pins.get("mapping_loop_turns"),
        "verifier_loop_turns": pins.get("verifier_loop_turns"),
        "outcome": pred.get("outcome"),
        "target_iri": pred.get("target_iri"),
        "confidence": pred.get("confidence"),
        "verifier_total": pred.get("verifier_total"),
        "normalized": normalize_prediction(row.get("mapping_object") or {}),
        "unstubbed_scores": not stub_scores,
        "honesty": (
            "model_requested is the configured id; echoed_model is server-reported "
            "from the pre-run probe only. Unstubbed = scores ≠ DeterministicMappingAgent "
            "0.92/20. Shim may still remap upstream — treat as live agent as-configured."
        ),
    }
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str), flush=True)
    print(f"Wrote {args.out}", flush=True)

    if not pred.get("rationale"):
        raise SystemExit("FAIL: empty rationale — suspect stub/broken agent")
    if stub_scores:
        raise SystemExit(
            "FAIL: confidence=0.92 and verifier=20 — looks like DeterministicMappingAgent"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
