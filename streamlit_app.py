"""SCUDO matching console — Streamlit frontend.

WHY THIS EXISTS
    On a locked-down desktop, Citrix group policy blocks
    node_modules/@esbuild/win32-x64/esbuild.exe, so `npm run dev` and
    `npm run build` both fail with spawn UNKNOWN and Vite cannot start. That
    leaves the React console unrunnable on the machine that needs it most.

    Streamlit is pure Python. No Node, no bundler, no esbuild, no build step.

WHAT IT TALKS TO
    The SCUDO package DIRECTLY — not over HTTP. `ingest_bytes`,
    `map_vendor_product` and `agent.run()` are plain Python functions in this
    repo, and `agent.run()` is a generator yielding exactly the events the SSE
    endpoint wraps. So there is no Flask process to start, no port to pick, no
    proxy to configure, and no SSE parsing.

    That also means this is not a second implementation of the matching logic.
    It is a different surface over the same code the API serves: same ladder,
    same gates, same agent.

RUN IT
    streamlit run streamlit_app.py

    Opens on http://localhost:8501. If that port is taken:
    streamlit run streamlit_app.py --server.port 8502

NOT A REPLACEMENT
    The React console (frontend/) remains the product UI. This is the
    fallback for environments where Node cannot execute, and a fast way to
    exercise the pipeline without a browser toolchain.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Environment BEFORE importing the package: config.py reads these at import
# time, so setting them afterwards is too late. Same ordering contract that
# start_local.py exists to enforce for the Flask app.
_BACKEND = Path(__file__).resolve().parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _best_local_store() -> str:
    """Pick the best store this checkout actually supports.

    Prefer the complete durable SciPy + SQLite store, then the legacy
    local-file journal, then memory. Detect support from files rather than
    importing config because Settings is created at module import time.
    """
    store_dir = _BACKEND / "scudo_mapping_mcp" / "store"
    if (store_dir / "scipy_sqlite_store.py").is_file():
        return "scipy_sqlite"
    if (store_dir / "local_file_store.py").is_file():
        return "local_file"
    return "memory"


_STORE = _best_local_store()
_EFFECTIVE_STORE = os.environ.get("STORE_BACKEND") or _STORE
os.environ["STORE_BACKEND"] = _EFFECTIVE_STORE
if not os.environ.get("SCUDO_PERSIST_TARGET", "").strip():
    os.environ["SCUDO_PERSIST_TARGET"] = _EFFECTIVE_STORE
os.environ.setdefault(
    "SCUDO_SCIPY_SQLITE_PATH",
    str(_BACKEND / ".local" / "scudo_matching.sqlite3"),
)
os.environ.setdefault("FRAME_SOURCE", "mock")
os.environ.setdefault("SCUDO_AUTH_ALLOW_DEV", "1")
os.environ.setdefault("SCUDO_AUTH_DEV_PRINCIPAL", "streamlit@local")
os.environ.setdefault("SCUDO_VERDICT_ALLOW_DEV", "1")
os.environ.setdefault("SCUDO_PERSIST_ALLOW_DEV_WRITES", "1")
os.environ.setdefault("CONSOLE_DB_BACKEND", "sqlite")
# Real agent by default (2026-08-14). All three levers fail SOFT, so a missing
# or expired key degrades to the deterministic path rather than erroring —
# judge a run by the reasoning trace, not by a number appearing.
#   local (NOT strands) is the working specialist: strands_specialist.py was
#   never built, so that backend abstains on every call.
os.environ.setdefault("SCUDO_AGENT_BACKEND", "bedrock")
os.environ.setdefault("SCUDO_SPECIALIST_BACKEND", "local")
os.environ.setdefault("SCUDO_DENSE_BACKEND", "opus")
# MUST accompany the opus dense arm: without it a Bedrock error RAISES and the
# whole match aborts (measured with a malformed key). With it, the dense arm
# degrades to Jaro-Winkler per candidate.
os.environ.setdefault("SCUDO_DENSE_FALLBACK", "1")

import streamlit as st  # noqa: E402

from scudo_mapping_mcp.agent import get_agent  # noqa: E402
from scudo_mapping_mcp.config import (  # noqa: E402
    PRIORITY_VENDORS,
    borderline_threshold,
    pass_threshold,
    settings,
)
from scudo_mapping_mcp.feedback import apply_decision  # noqa: E402
from scudo_mapping_mcp.frames import _read_vendor_frame  # noqa: E402
from scudo_mapping_mcp.ingest import ingest_bytes, seed_taxonomy  # noqa: E402
from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef  # noqa: E402
from scudo_mapping_mcp.store import get_store, storage_ready  # noqa: E402

st.set_page_config(
    page_title="SCUDO — vendor → CDAO matching",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Cognizant palette (see project memory `cognizant-palette`): brand blue on
# cream, near-black text. Kept in one place so the theme is legible rather than
# scattered through f-strings.
BLUE = "#1a5ecf"
CREAM = "#f5f3ef"
INK = "#1a1a1a"
MUTED = "#6b7280"
GREEN = "#0f7b3f"
AMBER = "#b45309"
RED = "#b91c1c"

_BAND_COLOUR = {"PASS": GREEN, "BORDERLINE": AMBER, "FAIL": RED}

# Models offered in the sidebar. All three verified callable with a Bedrock
# API key in us-east-1 on 2026-08-07. Opus first: it is the one the pipeline
# is specified against, and the others exist so a demo can show the
# cost/latency trade-off (Opus took ~14s end to end; the smaller models are
# quicker and visibly terser).
#
# The SCORE does not change between them — it is deterministic Jaro-Winkler
# computed by the matcher. Only the narration changes. Verified: scripted and
# Opus both returned confidence 0.851 / band pass / Equity Prices.
#
# One documented exception: with SCUDO_DENSE_BACKEND=opus the model IS on the
# score (memory_store.py:57->63->73->111 feeds Candidate.similarity).
#
# CORRECTED 2026-08-12. This comment used to claim the model "can only lower the
# deterministic anchor, never inflate it", citing the min(best, specialist) cap
# at matching.py:479. That is FALSE, and it was the dangerous kind of false: it
# reads as a safety guarantee. :479 is ONE of seven confidence branches, and it
# only applies when a specialist is configured AND concurs. The LLM score
# reaches published confidence UNCAPPED on four branches -- :348, :362, :444,
# :526 -- and it moves the number in EITHER direction, not just down.
#
# Two of those auto-map with no human review:
#   - :362  PASS band                  -> AUTO_MAPPED
#   - :444  borderline, no specialist  -> AUTO_MAPPED on this path
# The borderline branch splits on borderline_requires_specialist, which defaults
# to False (matching.py:165). The Flask route passes True (routes/mapping.py:569);
# Streamlit never does. So on THIS surface a borderline match auto-maps on the
# raw dense score. With shipped defaults (floor=0.75, half=0.05 -> pass=0.80,
# borderline=0.70) the auto-mapping window [0.75, 0.80) is non-empty.
#
# What actually makes the paragraph above safe is the DEFAULT, not a cap:
# SCUDO_DENSE_BACKEND defaults to "jaro_winkler" (config.py:301). Leave it unset
# and the score is deterministic and the model only narrates. Never cite a cap
# as the reason -- on the two auto-mapping branches no cap is involved.

# ONE region value for the whole app. The agent resolves AWS_REGION ->
# AWS_DEFAULT_REGION -> "eu-west-2" (agent.py:467-472); the preflight below and
# the model IDs must resolve the SAME way or the sidebar can go green on one
# region while the run that follows fails on auth in another. The default is
# eu-west-2 to match the agent, NOT us-east-1 — that mismatch was the defect.
SCUDO_REGION = (
    os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-west-2"
)

# Cross-region inference-profile IDs are region-bound: us.anthropic.* does not
# resolve in an EU region and vice versa. The families are NOT binary — Bedrock
# publishes us./eu./jp./au./global. prefixes — so map only the regions we have
# IDs for and fail closed everywhere else rather than silently handing
# ap-southeast-1 a us. profile that cannot resolve. Set SCUDO_BEDROCK_MODEL_ID
# explicitly to use a region this table does not cover.
_MODEL_PREFIX = (
    "eu."
    if SCUDO_REGION.startswith("eu-")
    else "us."
    if SCUDO_REGION.startswith("us-")
    else ""
)

BEDROCK_MODELS: dict[str, str] = (
    {
        "Claude Opus 4.8": f"{_MODEL_PREFIX}anthropic.claude-opus-4-8",
        "Claude Sonnet 4.5": f"{_MODEL_PREFIX}anthropic.claude-sonnet-4-5-20250929-v1:0",
        "Claude Haiku 4.5": f"{_MODEL_PREFIX}anthropic.claude-haiku-4-5-20251001-v1:0",
    }
    if _MODEL_PREFIX
    else {}
)

LINE = "#e3e0da"  # hairline rule — cream darkened, not a cold grey

st.markdown(
    f"""
    <style>
      /* Streamlit's own chrome — the rainbow top stripe and the Deploy
         button — is developer furniture. It is the loudest thing on the page
         and it is not ours, so it goes. The ⋮ menu stays: whoever is driving
         the demo still needs Rerun. */
      [data-testid="stDecoration"] {{ display: none; }}
      [data-testid="stDeployButton"] {{ display: none; }}
      header[data-testid="stHeader"] {{ background: transparent; }}

      html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                     "Helvetica Neue", Arial, sans-serif;
      }}
      .block-container {{
        padding-top: 1.8rem; padding-bottom: 3rem; max-width: 1180px;
      }}
      /* Streamlit stacks every element with the same generous gap, which
         reads as unconsidered. Tighten the default and let sections carry
         the rhythm instead. */
      [data-testid="stVerticalBlock"] {{ gap: .55rem; }}

      /* Section labels: small caps, not another competing headline. */
      .scudo-step {{
        font-size: .72rem; font-weight: 700; letter-spacing: .1em;
        text-transform: uppercase; color: {MUTED};
        margin: .2rem 0 .55rem 0;
      }}
      .scudo-step b {{ color: {BLUE}; font-weight: 700; }}

      /* The reasoning trace reads as a transcript. One continuous rail down
         the whole thing — zero vertical margin so the segments abut — and a
         two-column grid so wrapped body text aligns under itself rather than
         falling back under the tag. */
      .scudo-trace {{
        display: grid; grid-template-columns: 4.9rem minmax(0, 1fr);
        align-items: baseline; column-gap: .6rem;
        border-left: 2px solid {BLUE};
        padding: .3rem 0 .3rem .85rem; margin: 0;
        font-size: .88rem; line-height: 1.5;
      }}
      .scudo-trace > span {{ overflow-wrap: anywhere; }}
      .scudo-think {{ color: {INK}; font-style: italic; opacity: .82; }}
      .scudo-tool  {{ color: {INK}; }}
      .scudo-ret   {{ color: {MUTED}; }}
      .scudo-tag   {{
        font-size: .62rem; font-weight: 700; letter-spacing: .09em;
        text-transform: uppercase; color: {MUTED}; padding-top: .12rem;
      }}
      .scudo-pill {{
        display: inline-block; padding: .2rem .65rem; border-radius: 999px;
        font-size: .72rem; font-weight: 700; letter-spacing: .08em;
        color: #fff;
      }}
      /* IRIs are long and have no spaces; without this they run past the
         card edge instead of wrapping. */
      .scudo-iri {{ overflow-wrap: anywhere; word-break: break-word; }}
      code {{
        background: {CREAM} !important; color: {INK} !important;
        font-size: .82em !important; padding: .1em .35em !important;
      }}
      /* Ingest stage log: one tight block, not five loosely-spaced captions. */
      .scudo-stages {{
        font-size: .74rem; color: {MUTED}; line-height: 1.75;
        border-left: 2px solid {LINE}; padding-left: .8rem; margin: .1rem 0 .4rem;
      }}
      .scudo-stages b {{ color: {INK}; font-weight: 600; }}
      /* File uploader. At this column width the default dropzone wrapped
         "Browse files" onto two lines and broke the hint mid-list, which
         looked broken rather than tight. Shrink the copy and stop the button
         wrapping. */
      [data-testid="stFileUploaderDropzone"] {{
        padding: .75rem .9rem; background: {CREAM}; border: 1px dashed {LINE};
      }}
      [data-testid="stFileUploaderDropzoneInstructions"] span {{ font-size: .9rem; }}
      [data-testid="stFileUploaderDropzoneInstructions"] small {{ font-size: .72rem; }}
      [data-testid="stFileUploaderDropzone"] button {{ white-space: nowrap; }}

      /* Sidebar */
      [data-testid="stSidebar"] {{ border-right: 1px solid {LINE}; }}
      .scudo-side-fact {{
        display: flex; justify-content: space-between; gap: 1rem;
        font-size: .76rem; color: {MUTED}; padding: .22rem 0;
        border-bottom: 1px solid {LINE};
      }}
      .scudo-side-fact b {{ color: {INK}; font-weight: 600; }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner="Seeding CDAO taxonomy…")
def _bootstrap() -> tuple[object, int]:
    """Own and validate the process store, then seed the taxonomy once.

    cache_resource (not cache_data): this mutates a store rather than
    returning a value, and must run exactly once even though Streamlit
    re-executes this whole file on every interaction.
    """
    store = get_store()
    if not storage_ready(store):
        raise RuntimeError(
            "matching store storage/schema is unhealthy before taxonomy seed"
        )
    nodes = seed_taxonomy()
    if nodes <= 0 or not store.health():
        raise RuntimeError("matching store is unhealthy or empty after taxonomy seed")
    return store, nodes


# Band edges — ALWAYS via these two helpers, never the bare config calls.
#
# pass_threshold() / borderline_threshold() default their arguments to the
# MODULE constants (0.75 / 0.05 → 0.80 / 0.70). The engine gates on
# settings.confidence_floor / settings.borderline_half_width, which ARE read
# from the environment (matching.py resolves exactly these two when a caller
# passes no per-call override). So the no-arg form silently drifts from the
# engine on any tuned deployment: with CONFIDENCE_FLOOR=0.90 the bare call
# still says 0.80 while the gate is at 0.95 — the page would claim
# "PASS ≥ 0.80", draw the pass tick at 80%, and then render a FAIL pill on a
# 0.84 score. The widgets that exist to explain the gate must read from the
# same place the gate does.
def _pass_cut() -> float:
    return pass_threshold(settings.confidence_floor, settings.borderline_half_width)


def _borderline_cut() -> float:
    return borderline_threshold(
        settings.confidence_floor, settings.borderline_half_width
    )


def _band_of(confidence: float) -> str:
    if confidence >= _pass_cut():
        return "PASS"
    if confidence >= _borderline_cut():
        return "BORDERLINE"
    return "FAIL"


def _as_dict(event) -> dict:
    """Normalise an ``AgentEvent`` to the flat dict the SSE clients see.

    ``agent.run()`` yields ``AgentEvent`` objects with ``.type`` and a
    ``.payload`` dict — NOT dicts. The Flask route flattens them via
    ``to_json()`` (routes/mapping.py:1607), so consumers over HTTP see
    ``{"type": ..., **payload}``. Flattening the same way here keeps this
    surface and the API surface describing identical events, rather than two
    shapes that drift.
    """
    if isinstance(event, dict):
        return event
    payload = getattr(event, "payload", None) or {}
    return {"type": getattr(event, "type", None), **payload}


def _html(markup: str) -> str:
    """Strip per-line leading indentation from an inline HTML block.

    Streamlit renders markdown, and markdown turns any line indented by 4+
    spaces into a code block. Nesting an f-string fragment inside another
    f-string (the result card composes `score_block` / `mapped_block`) pushes
    the inner lines past that threshold, so the card rendered its own HTML
    source as a grey code box. Flattening the indentation keeps the Python
    readable and the output rendered.
    """
    return "\n".join(ln.strip() for ln in markup.splitlines() if ln.strip())


def _preflight_bedrock() -> dict:
    """Prove the bearer key actually works, before the demo does it live.

    A Bedrock API key is a single opaque bearer token that carries its own
    region and credentials — there is no access key, secret, session token or
    AWS_REGION to set. So the only meaningful check is: does an invoke
    succeed? Presence of the env var proves nothing; the token may be expired
    (they last ~12h) or scoped to a model this account cannot call, and both
    look identical until the first call.

    We therefore make ONE tiny converse() with maxTokens=8. It costs a
    fraction of a cent and is the only thing that distinguishes a working key
    from a plausible-looking one.

    Never raises — a preflight that crashes the page is worse than the failure
    it was checking for.
    """
    model_id = os.environ.get("SCUDO_BEDROCK_MODEL_ID", "")
    if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip():
        return {"ok": False, "message": "No API key set \u2014 paste one above."}

    try:
        import boto3
    except ImportError:
        return {"ok": False, "message": "boto3 is not installed."}

    try:
        # SCUDO_REGION, not a hardcoded us-east-1: the preflight must test the
        # same region the agent will use (agent.py:467-472), or the sidebar can
        # go green on one region while the run that follows fails on auth in
        # another — the same preflight-that-lies failure as the
        # Converse/ConverseStream bug below.
        client = boto3.client("bedrock-runtime", region_name=SCUDO_REGION)
        # ConverseStream, NOT Converse. The agent streams (Strands calls
        # ConverseStream), and the two are separately authorised: a key can
        # pass Converse and be denied ConverseStream. Testing the wrong one
        # produced a green "Ready" seconds before a live run failed on auth
        # — a preflight that lies is worse than no preflight.
        stream = client.converse_stream(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "ok"}]}],
            inferenceConfig={"maxTokens": 8},
        )
        # Auth can fail when the stream is CONSUMED, not when it is opened, so
        # drain it fully — `break` after the first event only proves the first
        # frame arrived and would miss a failure later in the stream, which is
        # exactly the demo-time failure this preflight exists to catch. The
        # response is capped at maxTokens=8, so draining is cheap.
        for _ in stream.get("stream", []):
            pass
    except Exception as exc:  # noqa: BLE001 \u2014 report the reason, whatever it is
        name = type(exc).__name__
        text = str(exc)
        if "ExpiredToken" in text or "expired" in text.lower():
            hint = " Bedrock API keys last ~12 hours \u2014 this one has expired."
        elif "AccessDenied" in name or "AccessDenied" in text:
            hint = f" The key cannot invoke `{model_id}` \u2014 request model access."
        elif "ValidationException" in name:
            hint = f" `{model_id}` may not exist in this key's region."
        else:
            hint = ""
        return {"ok": False, "message": f"Key rejected: {name}.{hint}"}

    label = next((k for k, v in BEDROCK_MODELS.items() if v == model_id), model_id)
    return {"ok": True, "message": f"Ready \u2014 {label} responded."}


def _render_event(event: dict) -> None:
    """One line of the agent's reasoning trace.

    The agent yields start · agent_message · tool_call · tool_result ·
    final_result · done. Rendering each as raw JSON is what the React console
    used to do, and it cut the agent's own sentences off mid-word — so each
    type gets the one field a reader actually needs.
    """
    kind = event.get("type")

    def line(tag: str, body: str, cls: str = "scudo-tool") -> None:
        st.markdown(
            f'<div class="scudo-trace"><span class="scudo-tag">{tag}</span>'
            f'<span class="{cls}">{body}</span></div>',
            unsafe_allow_html=True,
        )

    if kind == "agent_message":
        line("thinking", event.get("content", ""), "scudo-think")

    elif kind == "tool_call":
        args = event.get("args") or {}
        detail = (
            args.get("node_iri") or args.get("name") or args.get("product_id") or ""
        )
        suffix = f" &nbsp;<code>{detail}</code>" if detail else ""
        line("calls", f"<b>{event.get('tool')}</b>{suffix}")

    elif kind == "tool_result":
        result = event.get("result") or {}
        # The two agent backends disagree on this field's TYPE. The scripted
        # agent yields a dict; the Bedrock agent yields the MCP tool's raw
        # JSON *string*, because Strands passes tool output through verbatim.
        # Calling .get() on the string raised AttributeError and crashed the
        # whole page — and only on the Bedrock path, so it survived every
        # scripted test. Parse it, and if it is not JSON keep the text rather
        # than discarding it.
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                line("returns", result[:300], "scudo-ret")
                return
        if not isinstance(result, dict):
            line("returns", str(result)[:300], "scudo-ret")
            return
        candidates = result.get("candidates")
        if isinstance(candidates, list) and candidates:
            top = candidates[0]
            label = (top.get("node") or {}).get("label") or top.get("label") or "?"
            score = top.get("similarity", top.get("score"))
            score_txt = (
                f" &nbsp;<b>{score:.2f}</b>" if isinstance(score, (int, float)) else ""
            )
            summary = f"{len(candidates)} candidates · top: <b>{label}</b>{score_txt}"
        elif result.get("label"):
            summary = str(result["label"])
        elif isinstance(result.get("nodes"), list) and isinstance(
            result.get("edges"), list
        ):
            # Subgraph from get_ontology_neighbourhood. The generic fallback
            # below printed its raw keys — "root_iri, nodes, edges" — which
            # tells a reader nothing about what came back. Size does.
            summary = f"{len(result['nodes'])} nodes · {len(result['edges'])} edges"
        elif isinstance(result.get("count"), int):
            summary = f"{result['count']} results"
        else:
            summary = ", ".join(list(result)[:3]) or "ok"
        line("returns", summary, "scudo-ret")

    elif kind == "start":
        line(
            "start",
            f"{event.get('product_name') or event.get('product_id')} "
            f"&nbsp;<code>{event.get('agent_backend')}</code>",
            "scudo-ret",
        )

    elif kind in {"final_result", "done"}:
        return  # rendered by the result panel instead

    elif kind == "error":
        # Errors carry `error` (the exception) and usually `hint` (the
        # remediation). Dumping the raw JSON and clipping at 160 chars cut the
        # Bedrock hint off mid-sentence — right before it names the region and
        # SCUDO_BEDROCK_MODEL_ID, i.e. exactly the part that tells you how to
        # fix it. Render the fields instead of the dump.
        detail = str(event.get("error") or event.get("content") or "agent error")
        hint = str(event.get("hint") or "").strip()
        body = f'<span style="color:{RED}; font-weight:600;">{detail}</span>'
        if hint:
            body += f'<br><span style="color:{MUTED};">{hint}</span>'
        line("error", body, "scudo-ret")

    else:
        # Show unknown types rather than dropping them: a silently-swallowed
        # event is how a whole reasoning trace went unnoticed before. Prefer a
        # human-readable field when the event has one.
        readable = event.get("error") or event.get("content") or event.get("message")
        body = str(readable) if readable else json.dumps(event)[:400]
        line(str(kind), body, "scudo-ret")


# ── page ───────────────────────────────────────────────────────────────────

_store_resource, nodes = _bootstrap()

st.markdown(
    f"""
    <div style="border-left:4px solid {BLUE}; padding:.1rem 0 .1rem .95rem;
                margin:0 0 1.6rem 0;">
      <div style="font-size:1.75rem; font-weight:800; color:{INK};
                  letter-spacing:-.022em; line-height:1.1;">
        SCUDO <span style="color:{MUTED}; font-weight:500;">vendor → CDAO matching</span>
      </div>
      <div style="color:{MUTED}; font-size:.85rem; margin-top:.3rem;">
        Score a vendor product against the CDAO catalogue, and show the
        reasoning behind the score.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown('<div class="scudo-step">Run settings</div>', unsafe_allow_html=True)
    vendor = st.selectbox("Vendor", PRIORITY_VENDORS, index=0)
    _agent_opts = ["scripted", "bedrock", "azure"]
    _agent_default = (os.environ.get("SCUDO_AGENT_BACKEND") or "scripted").lower()
    provider = st.selectbox(
        "Agent",
        _agent_opts,
        index=_agent_opts.index(_agent_default) if _agent_default in _agent_opts else 0,
        help=(
            "'bedrock' is the real agent — reasoning loop, tool use, and LLM "
            "adjudication of borderline matches. 'scripted' is the offline "
            "narrator needing no AWS. The PASS/FAIL score stays deterministic "
            "either way; the model narrates and, on borderline cases, advises."
        ),
    )

    # ── Bedrock bearer key, entered at the podium ─────────────────────────
    #
    # A Bedrock API key is a SINGLE bearer token (AWS_BEARER_TOKEN_BEDROCK).
    # It carries its own region and credentials internally — no access key
    # id, no secret, no session token, no AWS_REGION. Asking for those was
    # wrong: they belong to the separate long-term-credential setup.
    # See https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html
    #
    # Keys are temporary (~12h), so they cannot be baked into a launch script
    # the day before a demo — hence pasting one here.
    #
    # NOT PERSISTED: set in os.environ for this process only, never written
    # to disk or logged, and masked so it stays out of a screen share.
    if provider == "bedrock":
        st.markdown(
            '<div class="scudo-step" style="margin-top:1.4rem;">Bedrock key</div>',
            unsafe_allow_html=True,
        )
        with st.form("bedrock_key", border=False):
            token = st.text_input(
                "API key",
                type="password",
                autocomplete="off",
                help=(
                    "The `bedrock-api-key-...` bearer token. Carries its own "
                    "region — nothing else to fill in."
                ),
            )
            # BEDROCK_MODELS is empty when this region has no inference-profile
            # prefix in the table above. Offering a picker there would hand out
            # IDs that cannot resolve, so the region falls back to whatever
            # SCUDO_BEDROCK_MODEL_ID says and the picker is simply not drawn.
            model_label = (
                st.selectbox("Model", list(BEDROCK_MODELS), index=0)
                if BEDROCK_MODELS
                else ""
            )
            applied = st.form_submit_button("Apply & test", use_container_width=True)

        if not BEDROCK_MODELS:
            st.caption(
                f"No inference-profile prefix known for `{SCUDO_REGION}` — set "
                "`SCUDO_BEDROCK_MODEL_ID` to a full model ID for this region."
            )

        if applied:
            if token and token.strip():
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token.strip()
            if model_label:
                os.environ["SCUDO_BEDROCK_MODEL_ID"] = BEDROCK_MODELS[model_label]
            st.session_state.creds_checked = _preflight_bedrock()

        # Keep the chosen model current even when the form is not resubmitted,
        # so switching model and pressing Run match cannot silently use the
        # previous one.
        if model_label:
            os.environ["SCUDO_BEDROCK_MODEL_ID"] = BEDROCK_MODELS[model_label]

        status = st.session_state.get("creds_checked")
        if status:
            (st.success if status["ok"] else st.error)(status["message"])

    # The run facts were a dense strip under the title, where they competed
    # with it. They are reference values, not headline — a client reads them
    # once and then wants them out of the way.
    # EFFECTIVE, not configured. The panel used to read "Dense arm: opus" even
    # after the breaker had tripped and every candidate was scored by
    # Jaro-Winkler — so the stated mitigation for silent fallback ("visibility")
    # did not actually hold. Also shows the Agent lever: it reported two of the
    # three.
    try:
        from scudo_mapping_mcp.opus_dense import dense_arm_status

        _dense_status = dense_arm_status()
    except Exception:  # noqa: BLE001 - the panel must never break the page
        _dense_status = {
            "effective": os.environ.get("SCUDO_DENSE_BACKEND", "jaro_winkler"),
            "degraded": False,
        }
    st.markdown(
        f"""
        <div style="margin-top:1.4rem;">
          <div class="scudo-step">This run</div>
          <div class="scudo-side-fact"><span>Taxonomy nodes</span><b>{nodes}</b></div>
          <div class="scudo-side-fact"><span>Pass at</span>
            <b style="color:{GREEN}">≥ {_pass_cut():.2f}</b></div>
          <div class="scudo-side-fact"><span>Borderline at</span>
            <b style="color:{AMBER}">≥ {_borderline_cut():.2f}</b></div>
          <div class="scudo-side-fact">
            <span>Agent</span><b>{os.environ.get("SCUDO_AGENT_BACKEND", "scripted")}</b></div>
          <div class="scudo-side-fact">
            <span>Specialist</span><b>{os.environ.get("SCUDO_SPECIALIST_BACKEND", "off")}</b></div>
          <div class="scudo-side-fact">
            <span>Dense arm</span><b style="color:{AMBER if _dense_status["degraded"] else INK}">{_dense_status["effective"]}{" (degraded)" if _dense_status["degraded"] else ""}</b></div>
          <div class="scudo-side-fact" style="border-bottom:none;">
            <span>Store</span><b>{os.environ["STORE_BACKEND"]}</b></div>
          <div class="scudo-side-fact" style="border-bottom:none; font-size:.68rem;">
            <span>{
            "matching state persists across restarts"
            if os.environ["STORE_BACKEND"] in {"scipy_sqlite", "local_file"}
            else "in-memory — forgets on restart"
        }</span><b></b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div style="margin-top:1.6rem; font-size:.74rem; color:{MUTED};
                    line-height:1.6;">
          No Node, no Vite, no bundler. This talks to the SCUDO package
          directly rather than over HTTP.
        </div>
        """,
        unsafe_allow_html=True,
    )

if "products" not in st.session_state:
    st.session_state.products = []

left, right = st.columns(2, gap="large")

# The two columns hold the CONTROLS only. The run itself renders below them at
# full width — previously the reasoning trace was squeezed into a half column
# while the other half sat empty under the products table, which wasted the
# widest part of the page on nothing and cramped the one thing worth reading.
choice: str | None = None
match_vendor: str = vendor  # vendor of the SELECTED contract; set by the picker
run_clicked = False

# ── 1. ingest ──────────────────────────────────────────────────────────────
with left:
    st.markdown(
        '<div class="scudo-step"><b>01</b> &nbsp;Upload vendor data</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"Uploading as **{vendor}** (change the vendor in the sidebar). Upload "
        "several vendors one after another — the list below accumulates."
    )
    upload = st.file_uploader(
        "CSV, TSV or JSON", type=["csv", "json", "tsv"], label_visibility="collapsed"
    )

    # Ready-made contract sets, so a demo does not depend on the presenter
    # having a suitable file to hand. These load through the SAME ingest_bytes
    # path as a real upload — no special-casing, so nothing here is a mock of
    # the pipeline, only a convenient source of bytes.
    _DEMO_SETS = {
        "Vendor Q (LSEG) — 3 contracts": (
            "LSEG",
            Path(__file__).resolve().parent
            / "sample_data"
            / "demo"
            / "vendorQ_LSEG_contracts.csv",
        ),
        "Vendor P (Bloomberg) — 2 contracts": (
            "Bloomberg",
            Path(__file__).resolve().parent
            / "sample_data"
            / "demo"
            / "vendorP_Bloomberg_contracts.csv",
        ),
    }
    _available = {k: v for k, v in _DEMO_SETS.items() if v[1].is_file()}
    if _available:
        # Expanded by default when nothing is loaded. A first-time user staring
        # at an empty uploader has no idea what a valid file looks like; hiding
        # the samples behind a closed expander is the single biggest reason a
        # demo stalls in the first thirty seconds.
        with st.expander(
            "Sample contract sets — load or download",
            expanded=not st.session_state.products,
        ):
            st.caption(
                "**Load** puts contracts straight into the matcher. "
                "**Download** gives you the CSV so you can see the expected "
                "shape and edit it — `product_id`, `name`, `description`."
            )
            for _label, (_v, _p) in _available.items():
                _lc, _dc = st.columns([2, 1])
                if _lc.button(
                    f"Load {_label}", key=f"demo_{_v}", use_container_width=True
                ):
                    st.session_state["_pending_demo"] = (_v, str(_p))
                    st.rerun()
                _dc.download_button(
                    "Download",
                    data=_p.read_bytes(),
                    file_name=_p.name,
                    mime="text/csv",
                    key=f"dl_{_v}",
                    use_container_width=True,
                )
            st.caption(
                "Load **both** to see two vendors' contracts matched against "
                "the same catalogue dataset — each keeping its own score and "
                "its own review decision."
            )

    # A sample-set button sets _pending_demo then reruns; ingest it here so it
    # goes through exactly the same code path as a browser upload.
    _pending = st.session_state.pop("_pending_demo", None)
    if _pending:
        _pv, _pp = _pending
        try:
            _frames = ingest_bytes(_pv, Path(_pp).name, Path(_pp).read_bytes())
        except (ValueError, OSError) as _exc:
            st.error(f"Could not load the sample set: {_exc}")
        else:
            _existing = {
                (p.get("vendor"), p["product_id"]): p for p in st.session_state.products
            }
            for _f in _frames:
                _existing[(_pv, _f.product_id)] = {
                    "vendor": _pv,
                    "product_id": _f.product_id,
                    "name": _f.name,
                }
            st.session_state.products = list(_existing.values())
            st.session_state.pop("last_decision", None)
            st.success(f"Loaded {len(_frames)} contract(s) for {_pv}")

    if upload is not None and st.button("Ingest", type="primary"):
        # Drop any pending decision. Re-ingesting can change a product's data
        # while keeping its vendor+product_id identity, which the staleness
        # guard at the foot of this file compares on — so without this an
        # Approve button left over from the previous file would still be live,
        # carrying that file's IRI and source-audit metadata.
        st.session_state.pop("last_decision", None)
        stages: list[str] = []
        try:
            frames = ingest_bytes(
                vendor,
                upload.name,
                upload.getvalue(),
                on_stage=lambda stage, detail: stages.append(
                    f"<b>{stage}</b> — "
                    + ", ".join(f"{k}={v}" for k, v in detail.items())
                ),
            )
        except ValueError as exc:
            # Client-shaped errors (bad JSON, too many rows) are the user's to
            # fix; show the message rather than a traceback.
            st.error(str(exc))
        else:
            # ACCUMULATE across vendors rather than replace. The demo's whole
            # point is many-to-one: Vendor Q's Contract X and Vendor P's
            # Contract Y both matched against the same catalogue dataset. If
            # this list is replaced on each upload, the second vendor's upload
            # erases the first from the picker and that story cannot be told —
            # even though the BACKEND kept both frames (a mismatch the old
            # handover doc listed as known issue #3).
            existing = {
                (p.get("vendor"), p["product_id"]): p for p in st.session_state.products
            }
            for f in frames:
                existing[(vendor, f.product_id)] = {
                    "vendor": vendor,
                    "product_id": f.product_id,
                    "name": f.name,
                }
            st.session_state.products = list(existing.values())
            st.success(
                f"Ingested {len(frames)} product(s) for {vendor} — "
                f"{len(st.session_state.products)} in total"
            )
            # One tight block: as five separate st.caption calls these got the
            # full inter-element gap each and sprawled down the page.
            st.markdown(
                '<div class="scudo-stages">' + "<br>".join(stages) + "</div>",
                unsafe_allow_html=True,
            )

    if st.session_state.products:
        st.dataframe(
            # Renamed for display only — session_state keeps the snake_case
            # keys the selectbox below indexes by. Vendor is shown because the
            # table now holds contracts from MORE THAN ONE vendor at a time.
            [
                {
                    "Vendor": p.get("vendor", "—"),
                    "Contract": p["product_id"],
                    "Name": p["name"],
                }
                for p in st.session_state.products
            ],
            use_container_width=True,
            hide_index=True,
        )
        _vendors_loaded = sorted(
            {p.get("vendor") for p in st.session_state.products if p.get("vendor")}
        )
        if len(_vendors_loaded) > 1:
            st.caption(
                f"{len(_vendors_loaded)} vendors loaded: {', '.join(_vendors_loaded)}. "
                "Contracts from different vendors can match the SAME catalogue "
                "dataset — that is expected, and each keeps its own score and "
                "its own review decision."
            )
        if st.button("Clear all", help="Empty the contract list and start again"):
            st.session_state.products = []
            st.session_state.pop("last_decision", None)
            st.rerun()

    # ── 1b. the THIRD upload point: catalogue datasets ─────────────────────
    #
    # Contracts are one side of the match; the CDAO catalogue is the other. It
    # ships as a fixture, so without this a demo can only match against the 14
    # nodes we chose — and the obvious client question ("can it match OUR
    # catalogue?") has no answer on screen.
    #
    # This adds nodes through the store's own upsert_taxonomy_node, i.e. the
    # same call the seeder uses, so an uploaded dataset is indistinguishable
    # from a seeded one and is immediately matchable.
    with st.expander("Add catalogue datasets (the other side of the match)"):
        st.caption(
            "CSV with columns `iri`, `label`, and optionally `parent_iri`. "
            "Uploaded datasets join the catalogue immediately and contracts "
            "can match against them."
        )
        # `parent_iri` MUST already exist — the store rebuilds a validated
        # snapshot on every write and raises on a dangling reference. Showing
        # the valid parents here turns an opaque ValueError into a choice.
        with st.popover("Valid parent IRIs"):
            for _n in sorted(
                _store_resource.list_taxonomy_nodes(), key=lambda n: n.iri
            ):
                st.markdown(f"`{_n.iri}` — {_n.label}")
        _ds_sample = (
            Path(__file__).resolve().parent
            / "sample_data"
            / "demo"
            / "catalogue_datasets.csv"
        )
        if _ds_sample.is_file():
            st.download_button(
                "Download dataset sample",
                data=_ds_sample.read_bytes(),
                file_name=_ds_sample.name,
                mime="text/csv",
                key="dl_datasets",
            )
        _ds_up = st.file_uploader(
            "Catalogue datasets CSV",
            type=["csv"],
            key="ds_upload",
            label_visibility="collapsed",
        )
        if _ds_up is not None and st.button("Add to catalogue", key="add_ds"):
            import csv as _csv
            import io as _io

            try:
                _rows = list(
                    _csv.DictReader(_io.StringIO(_ds_up.getvalue().decode("utf-8-sig")))
                )
            except (UnicodeDecodeError, _csv.Error) as _exc:
                st.error(f"Could not read that CSV: {_exc}")
                _rows = []
            _added, _skipped = 0, []
            for _i, _row in enumerate(_rows, start=2):  # row 1 is the header
                _iri = (_row.get("iri") or "").strip()
                _lbl = (_row.get("label") or "").strip()
                if not _iri or not _lbl:
                    # Report rather than silently drop — a half-loaded
                    # catalogue that says "3 added" is worse than an error.
                    _skipped.append(f"row {_i}: needs both iri and label")
                    continue
                try:
                    _store_resource.upsert_taxonomy_node(
                        TaxonomyNode(
                            iri=_iri,
                            label=_lbl,
                            parent_iri=(_row.get("parent_iri") or "").strip() or None,
                        )
                    )
                    _added += 1
                except ValueError as _exc:
                    # The commonest cause by far: parent_iri names a node that
                    # does not exist, so the snapshot rebuild refuses. Say that
                    # rather than echoing "taxonomy missing reference".
                    _txt = str(_exc)
                    if "missing reference" in _txt:
                        _skipped.append(
                            f"row {_i}: parent_iri does not exist yet — add the "
                            f"parent first, or leave parent_iri blank. ({_txt})"
                        )
                    else:
                        _skipped.append(f"row {_i}: {_txt}")
                except Exception as _exc:  # noqa: BLE001 — per-row, keep going
                    _skipped.append(f"row {_i}: {type(_exc).__name__}: {_exc}")
            if _added:
                # The sidebar node count is cached; it would keep the old
                # number while matching used the new catalogue.
                _bootstrap.clear()
                st.success(
                    f"Added {_added} dataset(s) to the catalogue. "
                    "Rerun a match to score against them."
                )
            for _msg in _skipped[:5]:
                st.warning(_msg)

# ── 2. match ───────────────────────────────────────────────────────────────
with right:
    st.markdown(
        '<div class="scudo-step"><b>02</b> &nbsp;Run the matcher</div>',
        unsafe_allow_html=True,
    )

    if not st.session_state.products:
        # st.info's saturated blue panel was the loudest element on an
        # otherwise calm page, and it shouted a message that is merely "not
        # your turn yet". A quiet placeholder says the same thing without
        # competing with the header for attention.
        st.markdown(
            f"""
            <div style="border:1px dashed {LINE}; border-radius:8px;
                        padding:.85rem 1rem; color:{MUTED}; font-size:.83rem;
                        line-height:1.5; background:#fff;">
              Waiting on step 01. The matcher scores the ingested frame, so a
              file has to land before a contract can be selected.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        # Index by (vendor, product_id), NOT product_id alone. Two vendors can
        # ship the same identifier, and matching uses the frame keyed by BOTH —
        # so a product_id-only picker would send the sidebar's vendor with
        # another vendor's contract and get a frame_not_found 404 that looks
        # like a broken matcher.
        _opts = [
            (p.get("vendor") or vendor, p["product_id"])
            for p in st.session_state.products
        ]
        _labels = {
            (p.get("vendor") or vendor, p["product_id"]): (
                f"{p.get('vendor') or vendor} · {p['product_id']} — {p['name']}"
            )
            for p in st.session_state.products
        }
        _picked = st.selectbox(
            "Contract",
            _opts,
            format_func=lambda k: _labels.get(k, k[1]),
            label_visibility="collapsed",
        )
        # match_vendor drives the run; the sidebar vendor only seeds NEW uploads.
        match_vendor, choice = _picked
        run_clicked = st.button("Run match", type="primary")

# ── 3. the run — full width ────────────────────────────────────────────────
if run_clicked and choice is not None:
    # Clear the previous decision before the run, not just after it. A run that
    # fails — frame read error, agent error, no final result — never reaches
    # the stash line below, so without this the buttons from the LAST
    # successful run stay live under a failed one and would record a decision
    # the reviewer never saw the result for. The run re-stashes on success.
    st.session_state.pop("last_decision", None)
    # _read_vendor_frame can RAISE, not just return None: under
    # FRAME_SOURCE=s3 a malformed object raises FrameDataError and a
    # transport failure raises ConnectionError. The Flask route wraps this
    # and returns a typed 502/500; unwrapped here it would put a red Python
    # traceback on screen, which is the worst thing to show a client. Latent
    # at the default FRAME_SOURCE=mock (always returns None), cheap to guard.
    try:
        ref = _read_vendor_frame(match_vendor, choice)
    except Exception as exc:  # FrameDataError / ConnectionError / NotImplementedError
        st.error(f"Could not read the vendor frame: {exc}")
        ref = None
        # Distinguish "read failed" from "not found": the not-found branch
        # below has its own, different and correct message.
        st.stop()
    if ref is None:
        # Mirrors the API's frame_not_found refusal: the system will not score
        # a product whose real details it does not have.
        st.error(
            f"No ingested frame for {match_vendor}/{choice}. Ingest it first — "
            "the matcher will not invent a name from the identifier."
        )
    else:
        st.markdown(
            f'<hr style="border:none; border-top:1px solid {LINE}; '
            'margin:1.8rem 0 1.3rem 0;">',
            unsafe_allow_html=True,
        )
        trace_col, result_col = st.columns([1.3, 1], gap="large")

        final = None
        # The selected agent can FAIL and the run still produces a final
        # result: the Bedrock backend yields its error, then runs the
        # deterministic matcher anyway (agent.py — "matcher runs regardless of
        # what the LLM recommended"). That is correct, because the score is
        # Jaro-Winkler and the model only narrates. But rendering the outcome
        # identically either way means a bedrock run with no credentials looks
        # pixel-identical to a successful one — so the dropdown appears
        # decorative, and someone could claim "Bedrock scored this 0.828" when
        # Bedrock never ran. Capture the error and say so.
        errored = None
        with trace_col:
            st.markdown(
                '<div class="scudo-step">Agent reasoning</div>',
                unsafe_allow_html=True,
            )
            box = st.container(border=True)
            with box:
                for raw in get_agent(provider).run(ref):
                    event = _as_dict(raw)
                    if event.get("type") == "error":
                        errored = (
                            event.get("error") or event.get("content") or "agent error"
                        )
                    if event.get("type") == "final_result":
                        final = event
                    _render_event(event)

        with result_col:
            st.markdown('<div class="scudo-step">Result</div>', unsafe_allow_html=True)
            if errored:
                # Do NOT claim the score is model-free. Under the shipped
                # default SCUDO_DENSE_BACKEND=opus the LLM's float per
                # candidate IS the published confidence (verified: a stubbed
                # 0.93/0.72 published as 0.93/0.72 and flipped auto_mapped ->
                # needs_review). Saying otherwise is a client-facing integrity
                # defect, so the wording now depends on which arm is live.
                _dense_live = (
                    os.environ.get("SCUDO_DENSE_BACKEND", "jaro_winkler").lower()
                )
                if _dense_live == "opus":
                    st.warning(
                        f"The **{provider}** agent did not complete: {errored}"
                        "\n\nThe narration is missing, and the LLM dense arm "
                        "(`SCUDO_DENSE_BACKEND=opus`) is enabled — so any "
                        "candidate it could not score fell back to "
                        "deterministic Jaro-Winkler. **Treat this score as "
                        "provisional and re-run once the agent is healthy.**"
                    )
                else:
                    st.warning(
                        f"The **{provider}** agent did not complete: {errored}"
                        "\n\nThe score below is still valid — with "
                        "`SCUDO_DENSE_BACKEND=jaro_winkler` it is computed "
                        "deterministically by the matcher, not by the model — "
                        "but no model narration was produced."
                    )
            if final is None:
                st.warning("Agent produced no final result.")
            else:
                mapping = final.get("mapping") or final
                confidence = float(mapping.get("confidence") or 0.0)
                band = mapping.get("band") or _band_of(confidence)

                # A confirmed precedent short-circuits retrieval, so a
                # human-confirmed match returns band "n/a" with an empty
                # candidate list while still carrying the confidence a human
                # signed off.
                #
                # Do NOT re-derive a band from that confidence. "n/a" is a
                # deliberate backend semantic, not a gap: matching.py:216-218
                # states precedent reuse is OUTSIDE the band model because the
                # case was already settled by a human, and models.py:189
                # documents it. Synthesising PASS here would show a green pill
                # the payload does not carry (Full result still says n/a) and
                # would re-classify an old approval against today's thresholds.
                #
                # The real problem is narrower — the card said "Not scored" for
                # the one result we are MOST sure of — so fix only that, below.
                confirmed = str(mapping.get("status") or "").upper() in {
                    "APPROVED",
                    "OVERRIDDEN",
                }

                band_txt = str(band).upper()
                colour = _BAND_COLOUR.get(band_txt, MUTED)
                # Degraded run: keep the band pill in its true colour (the
                # band IS that band) but drop the card's accent to neutral, so
                # a screenshot of a failed-agent run is not visually
                # indistinguishable from a clean green PASS.
                accent = MUTED if errored else colour
                node = (
                    mapping.get("mapped_node_label")
                    or mapping.get("mapped_node_iri")
                    or "—"
                )
                iri = mapping.get("mapped_node_iri") or ""
                status = str(mapping.get("status") or "—").replace("_", " ")
                # Hand the reviewer-decision block what it needs, but do NOT
                # draw the buttons here. Everything in this branch is gated by
                # `if run_clicked` (a plain st.button), which is False on the
                # rerun a button click causes — so a button drawn inside this
                # block is gone by the time its own click is handled, and the
                # click silently does nothing. Stash instead; draw at the
                # bottom of the file, outside the gate.
                st.session_state.last_decision = (
                    {"ref": ref, "iri": iri, "confidence": confidence} if iri else None
                )
                # "Not scored" is not the same claim as "scored zero". When no
                # candidate survives retrieval the payload carries band n/a,
                # confidence 0 and an empty candidate list — rendering that as
                # a hero "0.000" reads as a measured result and invites the
                # question "why is the match zero?", when the honest answer is
                # that nothing was ranked. Suppress the number and the bar.
                # ...with one exception: a human-confirmed precedent has no
                # candidates and band n/a, but it is not unranked — a human
                # ranked it. Show its confidence without touching the band, so
                # the number appears while the pill still honestly reads N/A.
                scored = (
                    band_txt not in {"N/A", "NONE", ""}
                    and bool(mapping.get("candidates"))
                ) or (confirmed and confidence > 0.0)
                # Confidence bar: the band edges are the story, so show where
                # this score landed rather than only the number. The ticks are
                # white notches cut THROUGH the fill — as coloured lines drawn
                # over the bar, the pass tick was green-on-green and vanished
                # exactly when the score passed, i.e. always in the demo.
                pct = max(0.0, min(1.0, confidence)) * 100
                b_pct = _borderline_cut() * 100
                p_pct = _pass_cut() * 100

                if scored:
                    score_block = f"""
                      <div style="font-size:2.6rem; font-weight:800; color:{INK};
                                  letter-spacing:-.03em; line-height:1.05;
                                  font-variant-numeric:tabular-nums;
                                  margin:.55rem 0 .1rem;">{confidence:.3f}</div>
                      <div style="color:{MUTED}; font-size:.72rem; letter-spacing:.08em;
                                  text-transform:uppercase; font-weight:700;
                                  margin-bottom:.85rem;">Confidence</div>

                      <div style="height:9px; background:{CREAM}; border-radius:99px;
                                  position:relative; overflow:hidden;">
                        <div style="width:{pct:.1f}%; height:100%; background:{colour};
                                    border-radius:99px 0 0 99px;"></div>
                        <div style="position:absolute; left:{b_pct:.1f}%; top:0;
                                    width:2px; height:100%; background:#fff;"></div>
                        <div style="position:absolute; left:{p_pct:.1f}%; top:0;
                                    width:2px; height:100%; background:#fff;"></div>
                      </div>
                      <div style="position:relative; height:1.1rem; font-size:.66rem;
                                  color:{MUTED}; margin-bottom:1.05rem;">
                        <span style="position:absolute; left:{b_pct:.1f}%;
                                     transform:translateX(-50%); top:.15rem;">{_borderline_cut():.2f}</span>
                        <span style="position:absolute; left:{p_pct:.1f}%;
                                     transform:translateX(-50%); top:.15rem;">{_pass_cut():.2f}</span>
                      </div>
                    """
                    mapped_block = f"""
                        <div style="color:{MUTED}; font-size:.68rem; letter-spacing:.09em;
                                    text-transform:uppercase; font-weight:700;
                                    margin-bottom:.2rem;">Mapped to</div>
                        <div style="font-size:1.02rem; font-weight:700; color:{INK};
                                    line-height:1.3;">{node}</div>
                        <div class="scudo-iri" style="color:{MUTED}; font-size:.72rem;
                                    margin-top:.3rem; font-family:ui-monospace,
                                    SFMono-Regular, Menlo, monospace;">{iri}</div>
                    """
                else:
                    # No score, no bar, no empty "Mapped to —". Say what
                    # happened instead, in the agent's own words.
                    # Just the verdict. An explanatory sentence here duplicated
                    # the rationale below almost word for word, and the status
                    # pill already says "needs review" — three ways of saying
                    # the same thing in one small card.
                    score_block = f"""
                      <div style="font-size:1.35rem; font-weight:700; color:{INK};
                                  margin:.6rem 0 .9rem;">Not scored</div>
                    """
                    rationale = str(mapping.get("rationale") or "").strip()
                    mapped_block = (
                        f"""
                        <div style="color:{MUTED}; font-size:.68rem; letter-spacing:.09em;
                                    text-transform:uppercase; font-weight:700;
                                    margin-bottom:.25rem;">Why</div>
                        <div style="color:{INK}; font-size:.82rem; line-height:1.5;
                                    opacity:.85;">{rationale}</div>
                        """
                        if rationale
                        else ""
                    )

                st.markdown(
                    _html(
                        f"""
                        <div style="border:1px solid {LINE}; border-top:3px solid {accent};
                                    border-radius:10px; padding:1.15rem 1.3rem 1.25rem;
                                    background:#fff;">
                          <div style="display:flex; align-items:center; gap:.65rem;
                                      flex-wrap:wrap;">
                            <span class="scudo-pill" style="background:{colour};">{band_txt}</span>
                            <span style="color:{MUTED}; font-size:.78rem;
                                         letter-spacing:.03em;">{status}</span>
                          </div>
                          {_html(score_block)}
                          <div style="border-top:1px solid {LINE}; padding-top:.85rem;">
                            {_html(mapped_block)}
                          </div>
                        </div>
                        """
                    ),
                    unsafe_allow_html=True,
                )

                with st.expander("Full result"):
                    st.json(mapping)


def _unused_ref_helper() -> VendorProductRef:  # pragma: no cover - typing anchor
    """Keeps the VendorProductRef import meaningful for readers tracing types."""
    return VendorProductRef(vendor="LSEG", product_id="X1")


# ---------------------------------------------------------------------------
# Reviewer decision — the "correct it" half of query-and-correct.
#
# Deliberately at column 0 at the end of the file, NOT in the result column.
# The whole result block is gated by `if run_clicked` (search for that name;
# the gate is the `if run_clicked and choice is not None:` line) where
# run_clicked is a plain st.button: Streamlit reruns the script on every click,
# and on that rerun run_clicked is False, so anything drawn inside that block
# no longer exists when its own click is processed. Buttons placed there fail
# SILENTLY — you click, nothing happens, and the natural conclusion is that
# apply_decision is broken. The run stashes into session_state instead
# (`st.session_state.last_decision = ...`) and the buttons live out here, where
# every rerun redraws them.
#
# apply_decision writes a precedent edge to whichever store is live. Under
# STORE_BACKEND=scipy_sqlite it is committed to the dedicated matching database,
# so the correction survives a restart and the next match of the same product
# short-circuits to the human's answer. No Aurora, no Bedrock.
_d = st.session_state.get("last_decision")
# Staleness guard. last_decision survives reruns, so after a match the user can
# switch vendor or product WITHOUT re-running and the buttons would still be
# holding the previous product's IRI — one click would then record a decision
# against a product nobody is looking at, silently and with a green success
# message. Only offer the buttons while the stash still matches what is
# selected on screen.
if _d and (_d["ref"].vendor != match_vendor or _d["ref"].product_id != choice):
    _d = None
if _d:
    st.markdown(
        '<div class="scudo-step">Reviewer decision</div>', unsafe_allow_html=True
    )
    _who = os.environ.get("SCUDO_AUTH_DEV_PRINCIPAL", "streamlit@local")
    _c1, _c2 = st.columns(2)
    # Both buttons must be drawn unconditionally on every rerun. Selecting the
    # verb with a ternary would short-circuit and skip drawing the second
    # button on the rerun where the first one fired.
    _ok = _c1.button("Approve", use_container_width=True)
    _no = _c2.button("Reject", use_container_width=True)
    _verb = "approve" if _ok else "reject" if _no else ""
    if _verb:
        # apply_decision raises a bare ValueError on eight paths
        # (feedback.py:87,91,93,98,106,114,125,129 — bad verb, missing
        # decided_by/node_iri/suggested_confidence, out-of-scope vendor,
        # unknown node, non-numeric or out-of-range confidence). Uncaught,
        # Streamlit renders that as a full-page red traceback — the same thing
        # the frame-read guard around the `_frame_for(...)` call exists to
        # prevent (search for "FrameDataError" in this file).
        #
        # The bare `except Exception` is deliberate and is NOT a swallow: this
        # is a UI boundary, and the failure modes are wider than ValueError.
        # The journal write is done BEFORE memory is updated, on purpose, and
        # local_file_store._write_locked lets the OSError reach the caller (its
        # docstring says so) — measured: an unwritable journal directory raises
        # PermissionError straight through, which is likely on a locked-down
        # desktop or a network share. A remote store adds more: taxonomy lookup
        # and the precedent write can raise RuntimeError or a backend client
        # error (neptune_store.py:520). Every one of them must show the reason
        # and must NOT print a success message.
        try:
            # suggested_confidence is passed on both verbs on purpose:
            # feedback.py:118-119 forces reject to 0.0 and ignores the value,
            # so one call site serves both.
            apply_decision(
                _d["ref"],
                decision=_verb,
                decided_by=_who,
                node_iri=_d["iri"],
                suggested_confidence=_d["confidence"],
            )
        except ValueError as _exc:
            st.error(f"Decision refused: {_exc}")
        except OSError as _exc:
            st.error(
                f"Could not write the decision journal — nothing was recorded. "
                f"Check SCUDO_MEMORY_PATH is writable. ({type(_exc).__name__}: "
                f"{_exc})"
            )
        except Exception as _exc:  # noqa: BLE001 — UI boundary; report, never swallow
            st.error(
                f"Decision failed — nothing was recorded. "
                f"({type(_exc).__name__}: {_exc})"
            )
        else:
            # Approve and reject are NOT symmetric, and saying "reuse your
            # decision" for both is false. Measured on local_file:
            #   approve -> get_precedent_mapping returns the node; the next
            #              match short-circuits to it
            #   reject  -> get_precedent_mapping returns None; the node is
            #              filtered out and the next match re-ranks without it
            #              (0.9083 equity-prices became 0.6138 fixed-income)
            st.success(
                "Approved — the next match of this product will reuse it."
                if _verb == "approve"
                else "Rejected — the next match of this product will exclude "
                "that node and re-rank without it."
            )
            # Clear the stash after a successful decision. The buttons stay on
            # screen across reruns, so without this a second click writes a
            # second identical journal record (measured: 2 lines for 2 identical
            # rejects) and FalkorDB deletes and recreates the edge with a fresh
            # decided_at. Re-run the match to decide again.
            st.session_state.pop("last_decision", None)


# ── 4. Ask the agent — free-text chat over the same tools ──────────────────
#
# WHY THIS IS DOWN HERE, not in a column: the reasoning trace and the chat
# transcript are both tall, and side-by-side they each get half the width and
# neither reads well. This is also OUTSIDE the `if run_clicked` block for the
# same reason the review buttons are (see the note above them): Streamlit
# reruns the script on every interaction, so anything inside that block is gone
# by the time its own widget is processed.
#
# The chat calls the SAME six tools the mapping agent uses. It cannot see data
# the pipeline cannot, and it does NOT score -- if a mapping comes out of a
# conversation, the number still comes from map_vendor_product via the tool.
st.markdown(
    f'<hr style="border:none; border-top:1px solid {LINE}; margin:2rem 0 1.3rem;">',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="scudo-step"><b>04</b> &nbsp;Ask the agent</div>',
    unsafe_allow_html=True,
)

if "chat" not in st.session_state:
    st.session_state.chat = []

# Honest labelling. With no Bedrock credentials the responder is a keyword
# router, and claiming otherwise is the kind of thing that gets noticed live.
# 'azure' is deliberately NOT forwarded: chat.py has no azure backend, and
# passing it through captioned a keyword responder as a "real tool-calling
# loop" (and, with SCUDO_AGENT_BACKEND=bedrock, ran Bedrock while claiming
# azure). The mapping agent still honours azure — only chat does not.
_chat_backend = "bedrock" if provider == "bedrock" else "scripted"
if provider == "azure":
    st.caption(
        "The Azure runtime has no chat backend — this chat uses the offline "
        "**scripted** responder. Select **bedrock** for real agent reasoning."
    )
if _chat_backend == "scripted":
    st.caption(
        "Scripted responder — answers a narrow set of questions using real "
        "catalogue data. Select the **bedrock** agent in the sidebar for "
        "open-ended reasoning with tool use."
    )
else:
    st.caption(
        f"**{_chat_backend}** — real tool-calling loop. The agent may call the "
        "matcher, read taxonomy nodes and inspect the hierarchy to answer."
    )

# Tell the user what the agent can currently SEE. Borrowed from the JPMC-side
# build, which gates its chat on "load both files first" — the useful half of
# that idea is stating the context, not disabling the box. A chat that silently
# knows nothing is worse than one that says so, and unlike a hard gate this
# still lets someone ask "how does scoring work?" before uploading anything.
if st.session_state.products:
    _v_loaded = sorted(
        {p.get("vendor") for p in st.session_state.products if p.get("vendor")}
    )
    st.caption(
        f"Context: **{len(st.session_state.products)} contract(s)** loaded from "
        f"**{', '.join(_v_loaded) or 'unknown vendor'}**, against a "
        f"**{nodes}-node** catalogue."
    )
else:
    st.caption(
        f"Context: no contracts loaded yet — the agent can explain the process "
        f"and the {nodes}-node catalogue, but cannot discuss a specific match "
        "until you load one in step 01."
    )

# Starter questions. A blank chat box gets blank stares in a demo; three
# concrete prompts get the first question asked. They set _chat_prefill, which
# the input below falls back to, so a click behaves exactly like typing.
_STARTERS = [
    "How do I start?",
    "How does the scoring work?",
    "Can two vendors match the same dataset?",
]
if not st.session_state.chat:
    _s1, _s2, _s3 = st.columns(3)
    for _col, _q in zip((_s1, _s2, _s3), _STARTERS):
        if _col.button(_q, key=f"starter_{_q[:12]}", use_container_width=True):
            st.session_state["_chat_prefill"] = _q
            st.rerun()

for _turn in st.session_state.chat:
    with st.chat_message(_turn["role"]):
        st.markdown(_turn["content"])
        for _t in _turn.get("tools") or []:
            st.markdown(
                f'<div class="scudo-trace"><span class="scudo-tag">calls</span>'
                f'<span class="scudo-tool"><b>{_t}</b></span></div>',
                unsafe_allow_html=True,
            )

if _q := (
    st.chat_input("e.g. why did that contract score 0.83?")
    or st.session_state.pop("_chat_prefill", None)
):
    st.session_state.chat.append({"role": "user", "content": _q})
    with st.chat_message("user"):
        st.markdown(_q)

    with st.chat_message("assistant"):
        _reply_parts: list[str] = []
        _tools_used: list[str] = []
        _box = st.container()
        try:
            from scudo_mapping_mcp.chat import get_chat_agent

            _agent = get_chat_agent(_chat_backend)
            # Replay prior turns so the agent has the thread, not just this line.
            _hist = [
                {"role": t["role"], "content": t["content"]}
                for t in st.session_state.chat[:-1]
            ]
            # Tell the agent what is actually on screen right now. Without
            # this it answered "there is no file-upload step in what I can
            # see" while the upload box sat directly above the chat.
            _prods = st.session_state.products
            if _prods:
                _vs = sorted({p.get("vendor") for p in _prods if p.get("vendor")})
                _names = ", ".join(
                    f"{p.get('vendor')}/{p['product_id']} ({p['name']})"
                    for p in _prods[:8]
                )
                _ui = (
                    f"{len(_prods)} contract(s) ingested from {', '.join(_vs)}: "
                    f"{_names}. Catalogue has {nodes} nodes. The user can run a "
                    "match on any of these in step 02."
                )
            else:
                _ui = (
                    "No contracts ingested yet. The user should use step 01 "
                    "above this chat — the drag-and-drop box, or the "
                    "'Load Vendor Q (LSEG)' / 'Load Vendor P (Bloomberg)' "
                    f"sample buttons. Catalogue has {nodes} nodes."
                )
            _ld = st.session_state.get("last_decision")
            if _ld:
                _ui += (
                    f" Last match: {_ld['ref'].vendor}/{_ld['ref'].product_id} "
                    f"scored {_ld['confidence']:.4f} onto {_ld['iri']}, "
                    "awaiting Approve/Reject."
                )
            for _ev in _agent.send(_q, history=_hist, ui_state=_ui):
                _kind = getattr(_ev, "type", None)
                _pay = getattr(_ev, "payload", {}) or {}
                if _kind == "agent_message":
                    _reply_parts.append(str(_pay.get("content") or ""))
                elif _kind == "tool_call":
                    _name = str(_pay.get("tool") or "")
                    if _name:
                        _tools_used.append(_name)
                        with _box:
                            st.markdown(
                                f'<div class="scudo-trace">'
                                f'<span class="scudo-tag">calls</span>'
                                f'<span class="scudo-tool"><b>{_name}</b></span></div>',
                                unsafe_allow_html=True,
                            )
                elif _kind == "error":
                    _detail = str(_pay.get("error") or "agent error")
                    _hint = str(_pay.get("hint") or "").strip()
                    _reply_parts.append(
                        f"**The agent could not complete.** {_detail}"
                        + (f"\n\n{_hint}" if _hint else "")
                    )
        except Exception as _exc:  # noqa: BLE001 — UI boundary: report, never crash
            _reply_parts.append(f"**Chat failed.** ({type(_exc).__name__}: {_exc})")

        _reply = "\n\n".join(p for p in _reply_parts if p).strip() or "(no reply)"
        st.markdown(_reply)

    st.session_state.chat.append(
        {"role": "assistant", "content": _reply, "tools": _tools_used}
    )
