#!/usr/bin/env python3
"""Generate a drawio diagram of the SCUDO agentic setup from the consolidated spec.

Input : docs/superpowers/scudo-agentic-spec.json  (components[], edges[], notes)
Output: docs/superpowers/scudo-agentic-architecture.drawio

Layout: one rounded container per `group`, arranged in flow columns; nodes stack
inside (wrapping into inner columns when a group is large). Node fill encodes
status (working/partial/stub/missing). Self-improving edges are tinted; config
edges are dashed. Reproducible: re-run after editing the spec.
"""

import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "scudo-agentic-spec.json")
OUT = os.path.join(HERE, "scudo-agentic-architecture.drawio")

STATUS_FILL = {
    "working": ("#d5e8d4", "#82b366"),  # green
    "partial": ("#fff2cc", "#d6b656"),  # yellow
    "stub": ("#ffe6cc", "#d79b00"),  # orange
    "missing": ("#f8cecc", "#b85450"),  # red
}
GROUP_TITLE = {
    "entrypoint": "Entry Points (two runtimes)",
    "orchestrator": "Lambda SCUDO Orchestrator — backend/scudo (LLM judges, code routes/gates)",
    "mcp_agent_host": "Mapping-MCP Runtime — backend/scudo_mapping_mcp (agent + matcher)",
    "matcher": "Matcher — 5-Rung Cost Ladder (gates)",
    "verifier": "Verifier + Gate",
    "graph_rdf_odrl": "Graph / RDF / ODRL",
    "self_improving": "Self-Improving Loop",
    "llm": "LLM — Claude Opus 4.8 on Bedrock (two configs)",
    "ingestion": "Ingestion",
    "storage": "Storage Seam (13-method contract + helpers)",
}
GROUP_HDR_COLOR = "#e1d5e7"  # container header tint (neutral); body transparent

# flow columns (left -> right); each entry is a vertical stack of groups
COLUMNS = [
    ["entrypoint", "ingestion", "storage"],
    ["orchestrator"],
    ["mcp_agent_host"],
    ["matcher"],
    ["verifier", "graph_rdf_odrl"],
    ["self_improving", "llm"],
]

# geometry
NODE_W, NODE_H = 220, 56
PAD_X, PAD_Y = 16, 14
HDR_H = 34
GROUP_VGAP = 46
COL_GAP = 70
MAX_ROWS = 8  # wrap a group into another inner column past this many nodes
TOP = 130  # y where group stacks begin (below title/legend)
LEFT = 40


def esc(s):
    return html.escape(str(s), quote=True)


def short(s, n=58):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    spec = json.load(open(SPEC))
    comps = spec["components"]
    edges = spec["edges"]
    by_id = {c["id"]: c for c in comps}
    group_of = {c["id"]: c["group"] for c in comps}
    groups = {}
    for c in comps:
        groups.setdefault(c["group"], []).append(c)

    cells = []  # (xml,) strings
    nid = {"n": 2}

    def new_id():
        nid["n"] += 1
        return f"c{nid['n']}"

    # --- containers + nodes, column-packed ---
    cell_for_comp = {}
    col_x = LEFT
    max_bottom = TOP
    for column in COLUMNS:
        col_w = 0
        y = TOP
        for g in column:
            members = groups.get(g, [])
            if not members:
                continue
            n = len(members)
            inner_cols = max(1, (n + MAX_ROWS - 1) // MAX_ROWS)
            rows = (n + inner_cols - 1) // inner_cols
            cw = PAD_X + inner_cols * (NODE_W + PAD_X)
            ch = HDR_H + PAD_Y + rows * (NODE_H + PAD_Y)
            col_w = max(col_w, cw)
            gid = new_id()
            title = GROUP_TITLE.get(g, g)
            cells.append(
                f'<mxCell id="{gid}" value="{esc(title)}" '
                f'style="rounded=1;arcSize=4;whiteSpace=wrap;html=1;fillColor=#f5f5f5;'
                f"strokeColor=#666666;verticalAlign=top;fontStyle=1;fontSize=12;"
                f'spacingTop=6;dashed=0;" vertex="1" parent="1">'
                f'<mxGeometry x="{col_x}" y="{y}" width="{cw}" height="{ch}" as="geometry"/></mxCell>'
            )
            for i, c in enumerate(members):
                ic = i // rows
                ir = i % rows
                nx = PAD_X + ic * (NODE_W + PAD_X)
                ny = HDR_H + ir * (NODE_H + PAD_Y)
                fill, stroke = STATUS_FILL.get(c["status"], ("#ffffff", "#666666"))
                cid = new_id()
                cell_for_comp[c["id"]] = cid
                label = (
                    f"<b>{esc(c['label'])}</b>"
                    f'<div style="font-size:9px;color:#555;">{esc(short(c["role"]))}</div>'
                )
                files = ", ".join(c.get("files", []) or [])
                tip = f"[{c['status']}] {c['role']}" + (
                    f"  |  {files}" if files else ""
                )
                cells.append(
                    f'<object id="{cid}" label="{esc(label)}" tooltip="{esc(tip)}" '
                    f'scudo_status="{esc(c["status"])}" scudo_group="{esc(c["group"])}">'
                    f'<mxCell style="rounded=1;arcSize=12;whiteSpace=wrap;html=1;'
                    f"fillColor={fill};strokeColor={stroke};fontSize=10;align=center;"
                    f'verticalAlign=middle;spacing=4;" vertex="1" parent="{gid}">'
                    f'<mxGeometry x="{nx}" y="{ny}" width="{NODE_W}" height="{NODE_H}" as="geometry"/>'
                    f"</mxCell></object>"
                )
            y += ch + GROUP_VGAP
        max_bottom = max(max_bottom, y)
        col_x += col_w + COL_GAP

    # --- edges ---
    for e in edges:
        s = cell_for_comp.get(e["source"])
        t = cell_for_comp.get(e["target"])
        if not s or not t:
            continue
        sg, tg = group_of.get(e["source"]), group_of.get(e["target"])
        if sg == "self_improving" or tg == "self_improving":
            stroke, dash = "#9673a6", "0"  # purple: feedback/learning
        elif e["source"] == "config":
            stroke, dash = "#999999", "1"  # dashed grey: config wiring
        else:
            stroke, dash = "#4d4d4d", "0"
        lab = esc(short(e.get("label", ""), 40))
        cells.append(
            f'<mxCell id="{new_id()}" value="{lab}" '
            f'style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;fontSize=9;'
            f"strokeColor={stroke};dashed={dash};endArrow=block;endFill=1;"
            f'labelBackgroundColor=#ffffff;" edge="1" parent="1" '
            f'source="{s}" target="{t}"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )

    # --- title + legend ---
    title_cell = (
        f'<mxCell id="title" value="SCUDO — Agentic Setup (vendor product → CDAO mapping)" '
        f'style="text;html=1;fontSize=20;fontStyle=1;align=left;verticalAlign=middle;" '
        f'vertex="1" parent="1"><mxGeometry x="{LEFT}" y="24" width="900" height="34" as="geometry"/></mxCell>'
    )
    sub_cell = (
        f'<mxCell id="subtitle" value="Generated from code map ({len(comps)} nodes / {len(edges)} edges). '
        f"TWO runtimes: (A) Lambda orchestrator (backend/scudo) — bare LLM judge + deterministic gate; "
        f"(B) mapping-MCP agent runner (scudo_mapping_mcp) — 4-tool loop + 5-rung matcher. "
        f'Shared storage seam. Self-improving loop tinted purple. Verified vs source 2026-06-18." '
        f'style="text;html=1;fontSize=11;align=left;verticalAlign=middle;fontColor=#555;" '
        f'vertex="1" parent="1"><mxGeometry x="{LEFT}" y="60" width="1300" height="40" as="geometry"/></mxCell>'
    )
    legend_items = [
        ("working ✅", "#d5e8d4", "#82b366"),
        ("partial 🟡", "#fff2cc", "#d6b656"),
        ("stub 🟠", "#ffe6cc", "#d79b00"),
        ("missing ❌", "#f8cecc", "#b85450"),
    ]
    lx = col_x  # to the right of the last column
    legend = [
        f'<mxCell id="legend" value="Status" style="rounded=1;whiteSpace=wrap;html=1;'
        f'fillColor=#ffffff;strokeColor=#666;verticalAlign=top;fontStyle=1;fontSize=12;spacingTop=6;" '
        f'vertex="1" parent="1"><mxGeometry x="{lx}" y="{TOP}" width="160" height="{40 + len(legend_items) * 34}" as="geometry"/></mxCell>'
    ]
    for i, (lab, fill, stroke) in enumerate(legend_items):
        legend.append(
            f'<mxCell id="lg{i}" value="{esc(lab)}" style="rounded=1;whiteSpace=wrap;html=1;'
            f'fillColor={fill};strokeColor={stroke};fontSize=10;" vertex="1" parent="legend">'
            f'<mxGeometry x="14" y="{34 + i * 34}" width="132" height="26" as="geometry"/></mxCell>'
        )

    # Page bounds from actual content so page-bounded exports don't crop the
    # rightmost column or the legend (codex: fixed 1600 cropped past x=2368).
    legend_h = 40 + len(legend_items) * 34
    page_w = lx + 160 + 40  # last column x + legend width + right margin
    page_h = max(max_bottom, TOP + legend_h) + 40

    body = "\n        ".join([title_cell, sub_cell] + legend + cells)
    xml = (
        '<mxfile host="app.diagrams.net" agent="scudo-codegen" version="24.0.0">\n'
        '  <diagram id="scudo-agentic" name="SCUDO Agentic Setup">\n'
        '    <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{page_w}" pageHeight="{page_h}" math="0" shadow="0">\n'
        "      <root>\n"
        '        <mxCell id="0"/>\n'
        '        <mxCell id="1" parent="0"/>\n'
        f"        {body}\n"
        "      </root>\n"
        "    </mxGraphModel>\n"
        "  </diagram>\n"
        "</mxfile>\n"
    )
    open(OUT, "w").write(xml)
    print(
        f"wrote {OUT} ({len(xml)} bytes); nodes={len(cell_for_comp)} edges_drawn={sum(1 for e in edges if cell_for_comp.get(e['source']) and cell_for_comp.get(e['target']))}"
    )


if __name__ == "__main__":
    main()
