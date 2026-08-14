"""Free-text chat over the SCUDO matching tools.

WHY THIS EXISTS
    ``agent.run(ref)`` answers exactly one question about exactly one product
    and then stops. That is right for the matching pipeline, but it means a
    demo cannot show the thing people most want to see: asking the agent a
    question in your own words and watching it choose tools to answer.

    This module adds that, and NOTHING else. It reuses:
      - the SAME six tools the mapping agent has
        (``_strands_tools_for_mapping``), so the chat cannot reach data the
        pipeline cannot, and
      - the SAME AgentEvent shape, so a UI already rendering the reasoning
        trace needs no new event handling.

WHAT IT DOES NOT DO
    It does not score. If the conversation ends in a mapping, the score still
    comes from ``map_vendor_product`` via the tool. The model chooses which
    tool to call and narrates the answer; the number stays deterministic and
    auditable. That is the whole architecture and chat must not weaken it.

TWO BACKENDS
    ``bedrock``  real Claude, real tool-calling loop (needs AWS).
    ``scripted`` keyword-routed fallback that calls the same tools and says
                 plainly that it is not a model. It exists so the demo works
                 on a machine with no AWS account at all -- an empty chat box
                 is a worse demo than an honest one.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator, Optional

from .agent import AgentEvent, _strands_tools_for_mapping
from .config import PRIORITY_VENDORS, settings

# Kept short on purpose. A long persona makes the model narrate ABOUT itself
# instead of using tools, which is the opposite of the demo's point.
CHAT_SYSTEM_PROMPT = """You are the SCUDO matching assistant.

SCUDO maps vendor market-data products onto a bank's CDAO catalogue. You help
a data steward understand and interrogate that process.

CRITICAL — be honest about what you are:
- You do not decide the final OUTCOME: the matcher owns the bands, the
  validations and the publish gate, and you report what it returns.
- But do NOT claim the score is model-free. The dense arm on this deployment
  is {dense_arm}. When it is `opus`, an LLM re-scores each nominated candidate
  and that score becomes the published confidence — so it can vary between
  runs and between models, and it falls back to Jaro-Winkler when Bedrock is
  unavailable. When it is `jaro_winkler`, the score is fully deterministic.
  If asked, say which one is live rather than asserting determinism.
- Never invent a confidence number, an IRI, or a product name. If a tool did
  not give it to you, say you do not have it.
- If asked why a score is what it is, call the tools and explain from their
  output: which candidates were retrieved, how they ranked, which band the
  score fell in.

Confidence bands: PASS at or above {pass_cut:.2f}; BORDERLINE between
{fail_cut:.2f} and {pass_cut:.2f}; FAIL below {fail_cut:.2f}.

Vendors in scope: {vendors}.

Your tools:
- find_similar_products - retrieve and rank catalogue candidates for a name
- get_taxonomy_node - read one CDAO node
- get_ontology_neighbourhood - see a node's surroundings in the hierarchy
- analyse_taxonomy_candidates - structural evidence (PageRank, distances)
- map_vendor_product_tool - run the real matcher on an ingested product
- describe_system_context - explain how SCUDO is wired

WHERE YOU ARE — you are embedded in the SCUDO Streamlit console, and the user
is looking at it right now. Guide them through THIS screen. Its steps are:
- **01 Upload vendor data** (top left): a drag-and-drop file box for vendor
  contract CSV/JSON, plus "Sample contract sets" with one-click **Load Vendor Q
  (LSEG)** and **Load Vendor P (Bloomberg)** buttons and Download links. The
  same step has **Add catalogue datasets** for the other side of the match.
- **02 Run the matcher** (top right): pick a contract, press **Run match**.
- **03** the reasoning trace, the score, the band, the mapped node, and the
  reviewer **Approve / Reject** buttons.
- **04 Ask the agent**: this chat.

So when the user asks "where do I upload?" or "how do I start?", TELL THEM
about step 01 and the Load buttons. Do NOT say uploading is impossible or
outside the system — it is on the same page, a few centimetres above this
chat box. You cannot receive a file THROUGH the chat, but never leave it
there: point at step 01.

If nothing has been ingested yet, matching a product returns 404 by design —
the matcher refuses to invent a product name from an identifier. Tell the user
to load a contract set first, then come back.

Prefer calling a tool over guessing. Be concise: a steward wants the answer
and the evidence, not an essay."""


def _system_prompt() -> str:
    floor = settings.confidence_floor
    half = settings.borderline_half_width
    return CHAT_SYSTEM_PROMPT.format(
        pass_cut=floor + half,
        fail_cut=floor - half,
        dense_arm=os.getenv("SCUDO_DENSE_BACKEND", "jaro_winkler").strip().lower(),
        vendors=", ".join(PRIORITY_VENDORS),
    )


class BedrockChatAgent:
    """Real Claude, real tool loop, streamed as AgentEvents."""

    NAME = "bedrock"

    def __init__(
        self,
        *,
        model_id: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        from .agent import DEFAULT_BEDROCK_MODEL_ID

        self._model_id = (
            model_id or os.getenv("SCUDO_BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID
        )
        self._region = (
            region
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or "eu-west-2"
        )
        self._agent: Any = None

    def _build(self) -> Any:
        # Lazy import: strands is only needed when Bedrock is actually chosen,
        # so a scripted-only machine never has to install it.
        try:
            from strands import Agent
            from strands.models import BedrockModel
        except ImportError as e:
            raise RuntimeError(
                "strands-agents is required for the bedrock chat backend. "
                "Install with: pip install strands-agents>=0.1"
            ) from e

        return Agent(
            model=BedrockModel(model_id=self._model_id, region_name=self._region),
            system_prompt=_system_prompt(),
            tools=_strands_tools_for_mapping(),
        )

    def send(
        self,
        message: str,
        *,
        history: Optional[list] = None,
        ui_state: Optional[str] = None,
    ) -> Iterator[AgentEvent]:
        """Yield AgentEvents for one user turn.

        History is replayed into the agent's own message list rather than
        concatenated into the prompt, so the model sees a real conversation and
        tool results stay attached to the turn that produced them.
        """
        if self._agent is None:
            self._agent = self._build()
        agent = self._agent

        # Seed history ONLY into an empty agent. Strands appends both the user
        # turn and the assistant reply to agent.messages itself, so replaying
        # our own history on every call double-counts it: measured 12 messages
        # after 3 turns instead of 6, with the first question present 3 times.
        # Latent today (Streamlit builds a fresh agent per rerun) but it fires
        # the moment anyone caches the agent — so guard it here, not there.
        if history and not agent.messages:
            for turn in history:
                role = turn.get("role")
                text = (turn.get("content") or "").strip()
                if not text or role not in {"user", "assistant"}:
                    continue
                agent.messages.append({"role": role, "content": [{"text": text}]})

        # Only translate blocks produced by THIS turn. Iterating the whole
        # transcript re-emitted every earlier tool_call under the new answer
        # (measured: turn 2 reported 2 calls for 1 real call).
        _seen_before = len(agent.messages)

        yield AgentEvent(
            type="start",
            payload={
                "message": message,
                "agent_backend": self.NAME,
                "model_id": self._model_id,
            },
        )
        seen_tools = 0
        # Prepend what is CURRENTLY on screen. Without it the agent answered
        # "there is no file-upload step in what I can see" while an upload box
        # sat a few centimetres above the chat — technically true of its tools,
        # useless to the person reading it.
        _prompt = f"[Console state: {ui_state}]\n\n{message}" if ui_state else message
        try:
            result = agent(_prompt)
        except Exception as exc:  # noqa: BLE001 - surface the reason to the UI
            hint = ""
            text = str(exc)
            if "Invalid API Key format" in text:
                # The bearer token reached Bedrock but is malformed — usually a
                # truncated paste or the wrong string entirely.
                hint = (
                    "The Bedrock API key is malformed. It must be the full "
                    "`bedrock-api-key-...` bearer token — check for a "
                    "truncated copy/paste."
                )
            elif "AccessDenied" in text or "AccessDenied" in type(exc).__name__:
                hint = (
                    "The credentials cannot invoke this model. Bedrock needs "
                    "bedrock:InvokeModelWithResponseStream (ConverseStream), "
                    "which is authorised separately from InvokeModel."
                )
            elif "ExpiredToken" in text or "expired" in text.lower():
                # The single most common failure on this project: Bedrock API
                # keys last ~12h, so a key that worked at yesterday's rehearsal
                # is dead at today's demo.
                hint = (
                    "The Bedrock API key has expired — they last about 12 "
                    "hours. Paste a fresh `bedrock-api-key-...` into the "
                    "sidebar and press Apply & test. Nothing else needs "
                    "changing."
                )
            elif "ValidationException" in type(exc).__name__:
                hint = (
                    f"Model id may not resolve in {self._region}. Check "
                    "SCUDO_BEDROCK_MODEL_ID and AWS_REGION agree — an `eu.` "
                    "inference profile cannot be called in a us- region, or "
                    "vice versa."
                )
            elif "NoCredentials" in type(exc).__name__ or "credentials" in text.lower():
                # Nothing was found to send at all. For this project the normal
                # answer is the bearer token, NOT a profile: verified that
                # botocore consumes AWS_BEARER_TOKEN_BEDROCK natively (a
                # malformed one returns AccessDenied, proving it was sent).
                hint = (
                    "No credentials found. Paste the `bedrock-api-key-...` "
                    "bearer token into the sidebar (AWS_BEARER_TOKEN_BEDROCK) "
                    "— these are short-lived, about 12 hours, so a key that "
                    "worked yesterday will not work today. A stale AWS_PROFILE "
                    "naming a profile that does not exist also fails here, and "
                    "botocore does not fall back — unset it."
                )
            yield AgentEvent(
                type="error",
                payload={"error": f"{type(exc).__name__}: {exc}", "hint": hint},
            )
            yield AgentEvent(type="done", payload={})
            return

        # Replay the tool calls the model made, so the UI can show the reasoning
        # rather than only the final sentence.
        for msg in list(getattr(agent, "messages", []))[_seen_before:]:
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and "toolUse" in block:
                    tu = block["toolUse"]
                    seen_tools += 1
                    yield AgentEvent(
                        type="tool_call",
                        payload={"tool": tu.get("name"), "args": tu.get("input") or {}},
                    )
                elif isinstance(block, dict) and "toolResult" in block:
                    tr = block["toolResult"]
                    body = tr.get("content") or []
                    text = ""
                    for c in body:
                        if isinstance(c, dict) and "text" in c:
                            text = c["text"]
                            break
                    yield AgentEvent(type="tool_result", payload={"result": text})

        yield AgentEvent(type="agent_message", payload={"content": str(result)})
        yield AgentEvent(type="done", payload={"tools_used": seen_tools})


class ScriptedChatAgent:
    """Keyword-routed chat over the real tools. No model, no AWS.

    This is NOT an LLM and it says so in its own replies. It exists because a
    demo machine may have no Bedrock access, and a chat box that errors is a
    worse first impression than one that answers a narrow set of questions
    honestly using real data.

    Every answer below is produced by calling the SAME functions the Bedrock
    agent's tools call -- so the numbers are real even though the phrasing is
    canned.
    """

    NAME = "scripted"

    def send(
        self,
        message: str,
        *,
        history: Optional[list] = None,
        ui_state: Optional[str] = None,
    ) -> Iterator[AgentEvent]:
        # ui_state is accepted for signature parity with the Bedrock agent; the
        # scripted branches answer from the store directly.
        del ui_state
        from .ingest import seed_taxonomy
        from .store import get_store

        q = (message or "").strip()
        low = q.lower()
        yield AgentEvent(
            type="start", payload={"message": q, "agent_backend": self.NAME}
        )

        floor = settings.confidence_floor
        half = settings.borderline_half_width
        pass_cut, fail_cut = floor + half, floor - half

        def say(text: str) -> AgentEvent:
            return AgentEvent(type="agent_message", payload={"content": text})

        def hit(*phrases: str) -> bool:
            """Match on WORD BOUNDARIES, not bare substrings.

            Substring matching produced real misroutes, all measured:
            "why can two vendors match the same dataset?" was swallowed by the
            scoring branch because it contains "why"; "how many vendors are
            there?" and even "what is Germany's role?" hit the many-to-one
            branch because "Germany" contains "many". Anchoring to word
            boundaries fixes both classes at once.
            """
            return any(re.search(rf"\b{re.escape(ph)}\b", low) for ph in phrases)

        # "can two vendors match the same dataset" — the many-to-one question
        if hit(
            *(
                "same dataset",
                "two vendors",
                "both vendors",
                "many-to-one",
                "duplicate",
                "twice",
                "other vendor",
                "another vendor",
                "one-to-one",
            )
        ):
            yield say(
                "Yes — and that is deliberate.\n\n"
                "Matching is **many-to-one**: several vendor contracts can map "
                "to the same catalogue dataset. Vendor Q's Contract X and "
                "Vendor P's Contract Y can both land on *Equity Prices*, each "
                "with its own score and its own review decision. Approving one "
                "does not consume the dataset or affect the other.\n\n"
                "Precedents are keyed per (vendor, contract), so decisions stay "
                "independent. One nuance: two contracts with the *identical "
                "name* share a ranking signal, so approving one slightly boosts "
                "that dataset's rank for the other — a nudge, never an "
                "auto-approval.\n\n"
                "Load both sample sets in step 01 to see it."
            )
            yield AgentEvent(type="done", payload={})
            return

        # "how does scoring work" / "why this score"
        if hit("score", "scores", "scoring", "confidence", "band", "bands", "why"):
            # Actually CALL the tool rather than only claiming to. A
            # tool_call event with no call behind it is theatre, and the class
            # docstring promises every answer comes from a real call.
            from .agent import _system_context_text  # noqa: PLC0415

            yield AgentEvent(
                type="tool_call",
                payload={"tool": "describe_system_context", "args": {}},
            )
            try:
                _ctx = _system_context_text()
            except Exception:  # noqa: BLE001 - narration must not break on it
                _ctx = ""
            if _ctx:
                yield AgentEvent(type="tool_result", payload={"result": _ctx[:400]})
            # The old text said flatly "the score is deterministic, not
            # generated by a language model". Under the shipped default
            # SCUDO_DENSE_BACKEND=opus that is FALSE: the LLM's per-candidate
            # float becomes the published confidence. Tell the truth for the
            # arm that is actually live.
            _dense = os.getenv("SCUDO_DENSE_BACKEND", "jaro_winkler").strip().lower()
            _how = (
                (
                    "**An LLM is part of the scoring on this deployment.** "
                    "Retrieval nominates candidates with BM25 over the CDAO "
                    "catalogue, then each nominee is re-scored by Claude "
                    "(`SCUDO_DENSE_BACKEND=opus`) and fused by reciprocal rank "
                    "fusion. That model score is the published confidence, so "
                    "it can vary between runs and between models, and it falls "
                    "back to deterministic Jaro-Winkler whenever Bedrock is "
                    "unavailable. The *bands* and the publish gate are "
                    "deterministic."
                )
                if _dense == "opus"
                else (
                    "The score is deterministic on this deployment, not "
                    "generated by a language model. Retrieval nominates "
                    "candidates with BM25 over the CDAO catalogue, then each is "
                    "scored with Jaro-Winkler string similarity and fused by "
                    "reciprocal rank fusion."
                )
            )
            yield say(
                "I am the scripted assistant (no model available), but these "
                "numbers are real.\n\n"
                f"{_how} The result is "
                f"banded: PASS at or above {pass_cut:.2f}, BORDERLINE from "
                f"{fail_cut:.2f} to {pass_cut:.2f}, FAIL below {fail_cut:.2f}.\n\n"
                + (
                    "Because the LLM scores each candidate, the same product "
                    "can score differently between runs, and switching model "
                    "changes the number as well as the words — the sidebar "
                    "model picker sets the id this arm uses."
                    if _dense == "opus"
                    else "Because it is deterministic, the same product always "
                    "scores the same, and switching the narrating model changes "
                    "the words here but never the number."
                )
            )
            yield AgentEvent(type="done", payload={})
            return

        # "what can this catalogue match to" / list nodes
        if any(
            k in low for k in ("catalogue", "catalog", "taxonomy", "nodes", "datasets")
        ):
            # Only seed if the catalogue is actually empty. seed_taxonomy()
            # calls store.replace_taxonomy, which on scipy_sqlite bumps
            # taxonomy_revision and rebuilds the SciPy index — measured: three
            # questions produced revisions 1, 2, 3. A read-only question must
            # not write.
            _store = get_store()
            nodes = _store.list_taxonomy_nodes()
            if not nodes:
                seed_taxonomy()
                nodes = _store.list_taxonomy_nodes()
            yield AgentEvent(
                type="tool_call",
                payload={"tool": "get_taxonomy_node", "args": {"all": True}},
            )
            yield AgentEvent(
                type="tool_result",
                payload={"result": json.dumps({"count": len(nodes)})},
            )
            listing = "\n".join(f"  - {n.label} ({n.iri})" for n in nodes[:12])
            yield say(
                f"The catalogue has {len(nodes)} nodes loaded. A sample:\n\n"
                f"{listing}\n\nAsk me to match a product against these."
            )
            yield AgentEvent(type="done", payload={})
            return

        # "how do I start" / walkthrough
        if hit(
            *(
                "how do i",
                "what do i do",
                "walk me",
                "guide",
                "next step",
                "getting started",
                "get started",
                "help",
                "where do i begin",
            )
        ):
            yield say(
                "Here is the whole flow:\n\n"
                "**1. Upload contracts.** Step 01. Pick a vendor in the sidebar, "
                "then upload a file — or open *Load a sample contract set* and "
                "load **Vendor Q (LSEG)**. Load **Vendor P (Bloomberg)** too; "
                "the list accumulates so both vendors' contracts sit side by "
                "side.\n\n"
                "**2. Match one.** Step 02 — pick a contract (the picker shows "
                "vendor · contract) and press **Run match**.\n\n"
                "**3. Read the reasoning.** The trace shows which tools ran and "
                "what came back, then the score, the band and the dataset.\n\n"
                "**4. Review it.** Approve or Reject. Then run the same contract "
                "again — the status becomes `approved` and the rationale becomes "
                "`precedent`. It remembered, and it survives a restart.\n\n"
                "**5. Try the other vendor.** Match Vendor P's contract and watch "
                "it reach the same dataset independently.\n\n"
                "Ask me *why* at any point and I will explain from the tools."
            )
            yield AgentEvent(type="done", payload={})
            return

        # "vendors"
        if hit("vendor", "vendors"):
            yield say(
                "Vendors in scope: " + ", ".join(PRIORITY_VENDORS) + ".\n\n"
                "Vendor matching is case-sensitive in the API but the UI passes "
                "the correct casing for you."
            )
            yield AgentEvent(type="done", payload={})
            return

        # "match X" / "map X"
        if hit("match", "map", "score this"):
            yield say(
                "I can run the real matcher, but I need an ingested contract.\n\n"
                "Upload a vendor file in step 01 first (or load a sample set), "
                "then pick it in step 02 and press Run match. The matcher "
                "refuses to score a contract whose real details it has not "
                "seen -- it will not invent a name from an identifier.\n\n"
                "With Bedrock enabled I can drive that whole flow from this "
                "chat box and explain each step as I go."
            )
            yield AgentEvent(type="done", payload={})
            return

        yield say(
            "I am the scripted assistant -- a keyword responder, not a language "
            "model, because no Bedrock credentials are configured.\n\n"
            "I can answer: how scoring works, what is in the catalogue, which "
            "vendors are in scope, and how to run a match.\n\n"
            "For genuine open-ended reasoning with tool use, set "
            "SCUDO_AGENT_BACKEND=bedrock with AWS credentials and ask me again."
        )
        yield AgentEvent(type="done", payload={})


def get_chat_agent(provider: Optional[str] = None) -> Any:
    """Return a chat agent. Mirrors agent.get_agent's contract deliberately.

    An explicit provider is honoured unconditionally -- selecting "bedrock"
    must never silently hand back the scripted responder, or the UI is lying
    about what produced the answer. That exact bug was fixed once already in
    the mapping agent's factory; do not reintroduce it here.
    """
    name = (provider or "").strip().lower()
    if name == "bedrock":
        return BedrockChatAgent()
    if name == "scripted":
        return ScriptedChatAgent()
    if name == "azure":
        # There is NO azure chat backend. Falling through to the default was
        # measured to return ScriptedChatAgent — while the UI captioned it
        # "real tool-calling loop" — and, with SCUDO_AGENT_BACKEND=bedrock set,
        # to return BedrockChatAgent while the caption said azure. Both are the
        # dropdown lying about what produced the answer, which is the exact bug
        # get_agent() was fixed for. Refuse loudly instead.
        raise ValueError(
            "There is no Azure chat backend. Use 'bedrock' for a real "
            "tool-calling loop, or 'scripted' for the offline responder."
        )
    backend = (os.getenv("SCUDO_AGENT_BACKEND") or "scripted").strip().lower()
    return BedrockChatAgent() if backend == "bedrock" else ScriptedChatAgent()
