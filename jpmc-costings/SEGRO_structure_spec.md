# SEGRO_source.xlsx — Structural Specification

Purpose: enable a fresh agent to build a re-contextualised (JPMC/AWS) version of this
workbook that preserves the same mechanics (named-range-driven live scenario model,
Gantt conditional-formatting bar system, chart types, status/provenance labelling)
while remapping vendor-specific content (SEGRO/Azure → JPMC/AWS).

## Table of contents

1. Named ranges (53 total) — targets + consuming sheets
2. Control Panel toggle mechanics + data validations
3. Status/provenance labelling + colour/number-format conventions
4. Cross-sheet formula patterns
5. Gantt mechanics (bar-drawing system, week columns, milestones)
6. Charts, conditional formatting summary, sheet order/hidden/tab-colours

---

## 1. Named ranges

53 defined names in `wb.defined_names`. All are workbook-scoped (no sheet-local
duplicates). Table below: name → target cell → consuming sheets (grep'd across
`extract/*.txt`) → purpose.

### 1a. Control Panel inputs (19 — the "amber cell" scenario switches)

| Name | Target | Consumed by | Purpose |
|---|---|---|---|
| `HCL_Toggle` | `'Control Panel>>>'!B5` | Overview, POAP Build Cost, 5yr Run Cost, Gantt-As-Drawn | Include/Exclude HCL delivery+licence lines |
| `EffortBasis` | `B6` | Overview, POAP Build Cost, R1 Pilot | Low/Likely/High effort column selector (drives `CHOOSE(MATCH(...))` on every person-day line) |
| `Platform` | `B7` | Overview, POAP Build Cost, 5yr Run Cost, Agents, Team Model, Rates, Gantt-As-Drawn | AKS/ARO container platform selector |
| `EnvCount` | `B8` | Overview, POAP Build Cost, 5yr Run Cost, Team Model | Number of environments (2/3/4), multiplies per-env infra lines |
| `ClusterCount` | `B9` | Benchmarks, 5yr Run Cost, POAP Build Cost, Team Model | Single/Dual cluster topology |
| `NodeSize` | `B10` | 5yr Run Cost, POAP Build Cost, Team Model, Rates | S/M/L node-pool SKU selector |
| `DayRate` | `B11` | Overview, POAP Build Cost, Rates | Blended day-rate override (GBP) |
| `ContingencyPct` | `B12` | Benchmarks, Overview, POAP Build Cost | Build contingency % uplift |
| `Indexation` | `B13` | 5yr Run Cost | Annual run-cost indexation % (years 2-5) |
| `SlipWeeks` | `B14` | Overview, POAP Build Cost, Gantt-Revised, Gantt-As-Drawn | Schedule slip in weeks — feeds both cost (WeeklyBurn×SlipWeeks) and Gantt bar-stretch formulas |
| `OverlapWeeks` | `B15` | POAP Build Cost, Overview, Gantt-Revised | HCL/build phase-overlap weeks — cost-only saving, does not move go-live |
| `OrgSize` | `B16` | Control Panel, Benchmarks, 5yr Run Cost, POAP Build Cost, Overview | Headcount for per-user licence lines |
| `Observability` | `B17` | Control Panel, 5yr Run Cost, Overview, POAP Build Cost, Rates, Gantt-As-Drawn | Azure-native vs Datadog observability stack |
| `ModelVendor` | `B18` | 5yr Run Cost, Overview, POAP Build Cost, Rates | Model-serving vendor family (AzureOpenAI/ClaudeOnAzure/GrokOnAzure/Llama) |
| `ModelSKU` | `B19` | Control Panel, 5yr Run Cost, Overview, POAP Build Cost, Rates | Foundation-model SKU; wrong-vendor SKUs evaluate to £0 via vendor/model gate formulas |
| `AgentStack` | `B20` | 5yr Run Cost, Agents, Control Panel, Overview, POAP Build Cost, Rates | Exclude/Include MCP+RAG+memory agent cost stack |
| `RagMemoryShape` | `B21` | 5yr Run Cost, Overview, Rates, Gantt-As-Drawn | AISearch_Cosmos vs CosmosOnly memory/vector store shape |
| `AgentCount` | `B22` | Overview, Agents, Control Panel, 5yr Run Cost | Agent-intensity multiplier (1–100) |
| `McpServerCount` | `B23` | 5yr Run Cost, Control Panel, Overview, Rates, Gantt-As-Drawn | MCP server / application-plug count (1–50) |

### 1b. Computed totals / headline outputs (11)

| Name | Target | Consumed by | Purpose |
|---|---|---|---|
| `TotalBuild` | `'POAP Build Cost'!I102` | Benchmarks, Overview, R1 Pilot | Grand total build cost (all phases + contingency) |
| `WeeklyBurn` | `'POAP Build Cost'!I99` | POAP Build Cost | Average weekly time-driven P1-P5 burn rate — drives slip cost |
| `TotalRun5yr` | `'5yr Run Cost'!K63` | Benchmarks, Overview, R1 Pilot | 5-year total run cost (indexed) |
| `RunY1..RunY5` | `'5yr Run Cost'!F63:J63` | Overview (RunY1 only referenced directly outside 5yr Run Cost) | Per-year run cost, Y1-Y5 |
| `PilotTotal` | `'R1 Pilot'!I17` | R1 Pilot | R1 pilot total (explicitly OUT OF TotalBuild/TotalRun5yr) |
| `GoLiveWeek` | `Overview!B25` | Overview | Nominal go-live week number |
| `GoLiveDate` | `Overview!B26` | (Overview only — no external grep hits; internal display cell) | Nominal go-live calendar date |
| `AgentStackRunY1` / `AgentStackMcpY1` / `AgentStackAgentY1` / `AgentStackFixedY1` | `'5yr Run Cost'!F64:F67` | 5yr Run Cost internal | Sub-component breakdown of Y1 agent-stack run cost |

### 1c. Agents-sheet internal cost-driver cells (7)

`Agent_AGT_OPEX`, `Agent_AGT_CAPEX`, `Agent_AGT_GROWTH`, `Agent_AGT_METER`,
`Agent_AGT_R1_CONTENT`, `Agent_AGT_RECON`, `Agent_AGT_ORCH` → all target
`Agents!F5:F11`. Named per-agent Y/N flags read by 5yr Run Cost / POAP Build Cost
formulas that gate agent-specific cost lines.

### 1d. Overview waterfall-chart helper cells (3)

`RefStackY1_1x2`, `RefStackY1_5x10`, `RefStackY1_30x50` → `Overview!D41:D43`.
Reference-stack sizing helper values used only inside Overview (no cross-sheet
consumption found).

### 1e. Gantt-internal helper names (8) — no cross-sheet consumers (grep confirmed 0 hits outside Gantt sheets themselves)

| Name | Target | Purpose |
|---|---|---|
| `GanttGoLive` | `'Gantt - POAP As Drawn'!BW4` | As-drawn nominal go-live week marker |
| `GanttCorrGoLive` | `'Gantt - Revised'!BX4` | Corrected/revised nominal go-live week marker (`=31+SlipWeeks`) |
| `GanttProbeEnd` | `'Gantt - POAP As Drawn'!J6` | Probe/anchor cell used by internal MEDIAN sub-step formulas |
| `GanttParentStart` / `GanttParentEnd` | `I147` / `J147` | Parent-row start/end for a collapsible task group |
| `GanttStepStart` / `GanttStepEnd` | `I152` / `J152` | Child-step row start/end (same pattern as parent) |
| `GanttFixedEnd` | `J192` | Fixed-schedule endpoint anchor |
| `GanttFoldFixedEnd` | `J147` | Folded/collapsed fixed-endpoint anchor (duplicate target of GanttParentEnd) |

These are internal cross-references within the (hidden) 'Gantt - POAP As Drawn'
sheet's own step-decomposition formulas (MEDIAN-based sub-row interpolation, same
technique documented in §5 for 'Gantt - Revised'), not cross-sheet named inputs.
A rebuild does not need to replicate every one of these verbatim — replicate the
*mechanism* (parent-row start/end anchors feeding child-step MEDIAN interpolation).

---

## 2. Control Panel toggle mechanics + data validations

Sheet `Control Panel>>>` (note trailing `>>>` in the sheet name — a visual
"start here" cue in the tab strip). Layout: column A = label, column B = the
live input cell (amber fill), column C = status label, column D = free-text
explanation of what the toggle drives. `freeze_panes = A3` (row 1 title + row 2
subtitle stay pinned; row 4 section header "SCENARIO SWITCHES & LOADINGS" scrolls
with the body). 19 input rows (B5:B23), rows 25-35 are a merged-cell "SCOPE NOTES"
block (11 caveats, e.g. HCL floor caveat, FX conversion rate, headcount
discrepancy) — plain prose, no formulas.

### Data validation dropdowns (all defined on Control Panel column B; `list`
type validations reference an inline comma-separated string, not a named range)

| Cell | Type | Options |
|---|---|---|
| B5 | list | `Include,Exclude` |
| B6 | list | `Low,Likely,High` |
| B7 | list | `AKS,ARO` |
| B8 | list | `2,3,4` |
| B9 | list | `Single,Dual` |
| B10 | list | `S,M,L` |
| B11 | decimal | (day-rate, no bounded list — free numeric input, min 0) |
| B12 | decimal | (contingency %, min 0) |
| B13 | decimal | (indexation %, min 0) |
| B14 | decimal | (slip weeks, min 0) |
| B15 | decimal | (overlap weeks, min 0) |
| B16 | decimal | (org size, min 1) |
| B17 | list | `Azure,Datadog` |
| B18 | list | `AzureOpenAI,ClaudeOnAzure,GrokOnAzure,Llama` |
| B19 | list | `GPT-4o,GPT-4.1,GPT-5.6-Terra,GPT-5.6-Sol,Claude-Sonnet-5,Claude-Fable-5,Grok-4.3,Grok-4.5,Llama` |
| B20 | list | `Exclude,Include` |
| B21 | list | `AISearch_Cosmos,CosmosOnly` |
| B22 | decimal | (agent count, min 1) |
| B23 | decimal | (MCP server count, min 1) |

Note B18/B19 is a **vendor+SKU pair**: formulas elsewhere use both together via a
"vendor_gate × model_gate" pattern — an AND-type formula check on both cells — so
that selecting a SKU that doesn't belong to the selected vendor zeroes the cost
line rather than erroring. This is the key mechanism to replicate for a JPMC/AWS
rebuild's model-vendor/model-SKU pair (e.g. `Bedrock,SelfHosted` ×
`Claude-Sonnet-5,Claude-Fable-5,Llama-4,Titan-Text`).

### The "everything recalculates live" architecture

Every other sheet (Overview, Rates, POAP Build Cost, 5yr Run Cost, R1 Pilot,
Agents, Team Model, Benchmarks, both Gantt sheets) references these 19 names
directly in formulas — there is no VBA/macro layer; it is pure formula
recalculation. Overview!A2 and POAP Build Cost!A2 both concatenate the live
scenario into a single human-readable banner string, e.g.:

```
="Active scenario:  effort = "&EffortBasis&"  ·  HCL = "&HCL_Toggle&" ..."
```

This is the pattern to replicate for a JPMC scenario banner — one concatenated
`&`-chain per sheet referencing the same named cells, so changing any Control
Panel toggle is visibly confirmed on every tab without re-deriving anything.

Amber fill (the editable-input convention) = **ARGB `FFFFF2CC`**, `patternType='solid'`,
applied uniformly to all 19 cells `B5:B23`. No border distinguishes them — fill
colour alone signals "editable". A rebuild should apply this same fill to the
JPMC/AWS-equivalent 19 input cells (e.g. `CloudProvider`, `Region`,
`ComputeSizeTier`, `ModelVendor`→Bedrock model family, etc.) and keep the same
list/decimal validation split.

---

## 3. Status/provenance labelling + colour/number-format conventions

### Status labels (6 distinct text values, one uniform font style — NOT colour-coded)

`VERIFIED`, `VERIFIED-ESTIMATE`, `INDICATIVE`, `DERIVED`, `ASSUMPTION`,
`RATE_MISSING` all appear across the "Status" columns (Rates!D, POAP Build
Cost!J, 5yr Run Cost, R1 Pilot!J, Team Model!E, Benchmarks!D). **Every single one
of these six labels renders in the identical font: bold, ARGB `FFCC171E` (a
dark red), no fill, no italics.** Confirmed by direct cell-by-cell comparison
across dozens of instances (Control Panel C5:C23, Team Model E5, Benchmarks D5,
Rates/POAP Build Cost/R1 Pilot status columns) — there is no colour distinction
between "this is solid VERIFIED data" and "this is a RATE_MISSING placeholder
£0"; meaning is conveyed entirely by the **text content** of the label, not by
colour. This is an important, non-obvious finding: a naive rebuild might assume
green=verified/red=missing or similar traffic-light coding — the source workbook
deliberately does not do this. Replicate as: single red bold font style, six
possible text values, reader must read the word.

Legend text (Control Panel!A2) spells out the six labels and their meaning
verbatim — reuse this same explanatory sentence (remapped) in the JPMC rebuild's
Control Panel banner.

### Other font/colour conventions

| Element | Style |
|---|---|
| Sheet title (row 1, col A) | 14pt bold white (`FFFFFFFF`) on fill `FF1F1F1F` (near-black); row height 18 (or 38 on sheets with a long merged subtitle, e.g. Rates/POAP Build Cost/5yr Run Cost) |
| Section header rows (e.g. Overview!A4 "HEADLINES", Control Panel!A4 "SCENARIO SWITCHES & LOADINGS", POAP Build Cost phase headers row 4) | bold white font on fill `FF595959` (mid-grey) |
| Column header row (e.g. Rates!row5, POAP Build Cost!row5) | bold, no explicit font colour override, fill `FFF2F2F2` (very light grey), thin bottom border |
| Subtotal rows (e.g. POAP Build Cost!row15 "SUBTOTAL — ...") | bold, fill `FFE2EFDA` (pale green) |
| Secondary/source-note text (e.g. "Azure" side label, basis/source columns) | font colour `FF808080` (mid-grey), not bold |
| Gantt legend text (row 2, merged) | italic, 12pt, `FF808080`, wrap_text=True |

### Number formats

| Format code | Used for | Sheets |
|---|---|---|
| `\£#,##0` | Whole-pound currency | POAP Build Cost, Rates, 5yr Run Cost, Overview, Benchmarks, R1 Pilot |
| `\£#,##0.00` | 2dp currency (per-unit rates) | 5yr Run Cost, POAP Build Cost, Rates, R1 Pilot |
| `\£0.0000` | High-precision per-unit rate (e.g. per-vCPU-hour) | Rates |
| `\£0.00000000` | Ultra-high-precision rate (e.g. per-token pricing) | Rates |
| `#,##0` | Plain quantity (person-days, node-hours) | POAP Build Cost, 5yr Run Cost, Control Panel, Overview, Benchmarks, R1 Pilot |
| `0.0%` | Percentage (headcount %, indirect %) | Benchmarks |
| `0.0` | FTE fraction (Team Model allocation) | Team Model |
| `dd\ mmm\ yyyy` | Calendar date | Overview (GoLiveDate) |
| `;;;` | **Hides all displayed content** (used only on Gantt bar cells L6:BT44 and K6:BS353) — see §5 | Both Gantt sheets |

### Column widths (representative, non-Gantt sheets)

Overview: A=53.2, B=15. Control Panel: A=46, B=16, C=18, D=84 (D is the wide
free-text explanation column). Rates: A=52, B=30, E=60, F=44. POAP Build Cost:
A=44, C=30, K=100 (K is the wide basis/source column). 5yr Run Cost: A=46, C=26,
M=44. R1 Pilot: A=46, C=34, K=54. Agents: B=42, G=54. Team Model: B=64, F=138
(very wide notes column). Benchmarks: A=56, E=60, F=40.

Pattern: label/name columns ~44-56 wide, short categorical columns 10-18, and
one "basis/source/notes" free-text column per sheet is deliberately very wide
(60-138) to hold full-sentence provenance narratives.

### Merged title/section rows

Every sheet has `A1:<lastcol>1` merged for the sheet title, and `A2:<lastcol>2`
merged for the scenario-banner/subtitle formula row. Control Panel additionally
merges each of its 11 "SCOPE NOTES" rows (A26:D26 through A35:D35) since each is
a full-sentence caveat spanning the label+status+notes columns. POAP Build
Cost/5yr Run Cost merge each phase-subtotal row (e.g. A104:K104…A111:K111) for
the same single-sentence-per-row reason.

### Freeze panes (full inventory)

Overview=A5, Gantt-POAP-As-Drawn=K6 (hidden sheet), Gantt-Revised=L6,
Control Panel=A3, Rates=A5, POAP Build Cost=A5, 5yr Run Cost=A5, R1 Pilot=A5,
Agents=A5, Team Model=A5, Benchmarks=A5. All sheets have `showGridLines=False`.
Both Gantt sheets freeze one column further right than the others (K/L vs A)
because their leftmost columns (Id, task name, Side, Status, Findings,
Depends-on, Start-wk, Wks, Live-start, Live-end, Change-notes) must stay pinned
while scrolling through the week-number bar columns to the right.

---

## 4. Cross-sheet formula patterns

### 4a. Effort-basis selector (every person-day cost line)

```
G6: =CHOOSE(MATCH(EffortBasis,{"Low","Likely","High"},0),D6,E6,F6)
```
Columns D/E/F hold the Low/Likely/High quantity estimates (person-days,
node-hours, GB, etc.); G is the "live" quantity that recalculates when the
Control Panel `EffortBasis` toggle changes. This exact 3-way CHOOSE/MATCH
pattern appears on nearly every quantity line across POAP Build Cost, 5yr Run
Cost, and R1 Pilot. Replicate verbatim for the JPMC rebuild's effort ranges.

### 4b. Rate lookup + cost calculation

```
H6: =Rates!$C$96          (rate/unit, looked up from the Rates sheet)
I6: =G6*Rates!$C$96        (cost = live qty × rate)
```
All rates live in one central `Rates` sheet; every cost line elsewhere is a
pure `qty × Rates!$C$n` reference — never a hardcoded rate. This centralisation
is what makes DayRate/Indexation/ContingencyPct overrides propagate everywhere.

### 4c. Toggle-gated cost lines (zero-if-excluded / zero-if-wrong-vendor)

```
I90: =IF(HCL_Toggle="Include",G90*Rates!$C$106,0)
I31: =IF(Platform="ARO",(G31*Rates!$C$9)*IF(ClusterCount="Dual",2,1),0)
```
Pattern: wrap the qty×rate multiplication in an `IF(<toggle>="<value>", ..., 0)`
so a line only contributes cost when its governing toggle matches. Nested
`IF(ClusterCount="Dual",2,1)` multipliers handle "doubles this fee only, not
node count" business rules (§ Control Panel note on ClusterCount). The
vendor/model-SKU gate (§2) uses the same technique with two nested conditions.

### 4d. Per-environment / per-user scaling

```
I32: =(G32*Rates!$C$14)*EnvCount/4
I92: =G92*Rates!$C$133          where G92: =OrgSize
```
Quantities baselined at a nominal env count (4) are rescaled by `EnvCount/4`;
per-user lines multiply the rate directly by the `OrgSize` named cell.

### 4e. Phase subtotals and roll-ups

```
I75: =SUM(I66:I74)                              (phase subtotal)
I97: =I15+I50+I62+I75+I85+I94                    (build grand total = sum of subtotals)
I98: =I97*ContingencyPct                          (contingency uplift, DERIVED)
I102 (=TotalBuild): I97+I98
```

### 4f. Weekly time-driven burn rate + slip/overlap cost (the schedule↔cost link)

```
I99 (WeeklyBurn):
  =(SUMIF($L$6:$L$14,"TD",$I$6:$I$14)
   +SUMIF($L$19:$L$49,"TD",$I$19:$I$49)
   +SUMIF($L$54:$L$61,"TD",$I$54:$I$61)
   +SUMIF($L$66:$L$74,"TD",$I$66:$I$74)
   +SUMIF($L$79:$L$84,"TD",$I$79:$I$84))/29

I100 (slip cost):   =SlipWeeks*WeeklyBurn
I101 (overlap saving): analogous SUMIF pattern over the same TD-tagged ranges,
                        netting off double-running Azure environment cost for
                        parallel HCL-install weeks.
```
Column `L` on POAP Build Cost / R1 Pilot tags every cost line `TD` (time-driven:
people effort + Azure commissioning windows — these lines' *duration* stretches
proportionally with `SlipWeeks`) or `FIXED` (fixed-fee engagements — licences,
one-off pen-test/red-team engagements, training — these shift in time but do
NOT change duration/cost when slip is applied). `SUMIF(...,"TD",...)` sums only
the TD-tagged subset within each phase's row-range, divides by the nominal
29-week programme span to get a weekly run-rate, then multiplies by
`SlipWeeks` to get the cost impact of slipping the schedule. **This TD/FIXED tag
is the single most important mechanism to replicate for a JPMC rebuild** — it is
what makes the cost model schedule-aware without a separate resourcing engine.

### 4g. Vendor/model gate (Model SKU validity)

Cost lines referencing model-serving (tokens, GPU, inference) wrap their rate
lookup in a compound gate checking BOTH `ModelVendor` and `ModelSKU` agree (e.g.
GPT-4o only prices under `ModelVendor="AzureOpenAI"`); mismatched combinations
evaluate the line to £0 rather than raising a formula error. Replicate as
`IF(AND(ModelVendor="Bedrock",ModelSKU="Claude-Sonnet-5"), qty*rate, 0)` per SKU
row on the JPMC/AWS Rates sheet.

### 4h. Overview waterfall/cumulative helper columns (base+block+delta+cumulative)

```
B14 (invisible base): =MIN(E13,E14)
C14 (visible block):  =ABS(D14)
D14 (signed delta):   ='POAP Build Cost'!I50
E14 (cumulative):     =E13+D14
```
Each waterfall segment row computes an invisible "base" (the running total up
to this segment) and a visible "block" (the segment's own magnitude), feeding a
stacked bar chart where the base series is rendered transparent/no-fill and only
the block series shows colour — the classic Excel waterfall-without-a-native-
waterfall-chart-type technique. A mirrored reverse-cumulative I/J/K column set
(H12:K18) builds the same chart read right-to-left for the second Overview
chart. Replicate this base/block helper-column pattern for the JPMC rebuild's
cost waterfall.

### 4i. Scenario banner string (concatenation, not display formatting)

```
Overview!A2 / 'POAP Build Cost'!A2:
="Active scenario:  effort = "&EffortBasis&"  ·  HCL = "&HCL_Toggle&" ...
  ·  day rate £"&TEXT(DayRate,"#,##0")&" ..."
```
`TEXT()` wraps numeric named cells so the concatenated banner renders formatted
numbers (e.g. "£850" not "850"). Every sheet that displays a scenario summary
uses this same `&`-chain-of-named-cells pattern.

---

## 5. Gantt mechanics — 'Gantt - Revised' (visible sheet, the one to model for JPMC)

`freeze_panes = L6` (rows 1-5 + columns A-K stay pinned). Dimensions `A1:BZ44`.
Row 1 = sheet title (merged A1:BV2 together with row2... actually row1 is title,
`A2:BV2` merged = legend paragraph, italic 12pt grey, wrap_text). Row 3 = month
labels (bold, spans ~4-5 week-columns each via visual grouping, not true merge).
Row 4 = week-number header (1, 2, 3 … 61) — column L holds week 1, column M week
2, etc., i.e. **each week is exactly one spreadsheet column**, running from
column L (week 1) to column BT (week 61). Row 4 also holds three helper cells
off to the right: `BX4 = 31+SlipWeeks` (nominal go-live week, named
`GanttCorrGoLive`), `BY4 = 29+SlipWeeks` (range-band start), `BZ4 = 33+SlipWeeks`
(range-band end).

### 5a. Task-row columns (A:K, fixed per row)

| Col | Header | Contents |
|---|---|---|
| A | Id | short code, e.g. `G-P2-L4-R` (G=Gantt, P2-L4=phase-line ref, R=Revised) |
| B | Major line of work / technical step | task name; child steps indented with `    • ` prefix |
| C | Side | `SEGRO` / `HCL` / `Azure` / `ThirdParty` — drives bar colour on the As-Drawn sheet (not on Revised, see 5d) |
| D | Status | e.g. `RECOMMENDED` |
| E | Findings | review-finding tag (F1, F2, F17, …) |
| F | Depends on | dependency reference |
| G | Start wk | **nominal** (pre-slip) start week number, an integer 1-61 |
| H | Wks | nominal duration in weeks |
| I | Live start | **formula-computed**, see 5b |
| J | Live end | **formula-computed**, see 5b |
| K | Change vs as-drawn | free-text note explaining the revision |

Rows: parent task rows (e.g. row 6, 10, 15, 20, 25, 30, 37, 41) each followed by
2-3 indented child-step rows that are **collapsed by default** via Excel row
outline grouping — `outlineLevel=1`, `hidden=True` on every child row (e.g. rows
7-8, 11-13, 16-18 …), with `sheetPr.outlinePr.summaryBelow=False` (meaning the
parent/summary row is ABOVE its children, standard Excel "expand down" grouping).
Clicking the outline `+` control next to a parent row reveals its child steps.

### 5b. The Live-start/Live-end schedule-transform formulas (the core mechanism)

For a **parent** row (e.g. row 6, nominal start=G6, duration=H6):

```
I6 (Live start): =MIN(61,IF(G6<=29, ROUND((G6-1)*(29+SlipWeeks)/29,0)+1, G6+SlipWeeks))
J6 (Live end):   =MIN(61,IF(G6+H6-1<=29, MAX(ROUND((G6+H6-1)*(29+SlipWeeks)/29,0),I6), G6+H6-1+SlipWeeks))
```

Logic: the nominal programme has a 29-week "core" span (weeks 1-29, i.e. up to
and just before the original go-live). Two regimes:
- **Within the 29-week core** (`G<=29` / `G+H-1<=29`): the week number is
  **proportionally stretched** — `(nominal_week-1) * (29+SlipWeeks)/29 + 1` —
  so tasks compress/expand together to fill the new (29+SlipWeeks)-week core,
  keeping their *relative* position in the timeline constant.
- **Beyond the 29-week core** (hypercare, post-go-live tail): the week is
  **additively shifted** — `nominal_week + SlipWeeks` — i.e. it just moves
  later by the slip amount, no proportional stretch (there's nothing to
  compress against past the core span).
- The whole result is clamped to `MIN(61, ...)` — 61 is the last visible week
  column (BT) — so a very large slip can't push bars off the printed grid;
  instead column `BU` ("Slip") flags `"▶ clipped"` when the true unclamped end
  exceeds 61 (see the `BU6` formula, §5e).

For a **child/step** row (e.g. row 7, nested inside parent row 6):

```
I7: =MEDIAN($I$6,$J$6,$I$6+ROUND((G7-$G$6)/$H$6*($J$6-$I$6+1),0))
J7: =MEDIAN(I7,$J$6,$I$6+ROUND((G7+H7-$G$6)/$H$6*($J$6-$I$6+1),0)-1)
```
Logic: child steps don't get their own independent slip transform — instead
they are **linearly interpolated within their already-transformed parent's
Live-start/Live-end window**, proportional to where the child's nominal
start/duration sits within the parent's nominal span. The `MEDIAN(...)` wrapper
is a clamp-to-parent-bounds trick (median of 3 values where two are the
parent's own bounds forces the result to never fall outside them).

**To build a fresh 14-week JPMC Gantt** (kickoff 2026-07-27 → go-live
2026-11-01, i.e. a 14-week nominal core instead of 29): replace `29` with `14`
(or whatever the new nominal core-span constant is) in every parent-row I/J
formula, keep the child-row MEDIAN-interpolation formula unchanged (it has no
hardcoded 29), and set week-column 1 = w/c 2026-07-27. Recompute `GanttCorrGoLive`
analog as `=<nominal_golive_week>+SlipWeeks` (e.g. if go-live is nominally week
14, `=14+SlipWeeks`), and the range-band start/end as `±2` weeks around it (the
source uses `29±2` → `29`/`31`/`33`; scale proportionally or keep ±2 weeks
literal, whichever the reviewer prefers — the source's own choice of ±2 is
arbitrary flex, not derived from 29).

### 5c. Week-column month header (row 3)

Row 3 has one text label per **first column of each new month** (e.g. `L3='Jul 26'`
where L=week1, next label `O3='Aug 26'` at week4, `T3='Sep 26'` at week9, etc.)
— i.e. month boundaries are manually placed at whichever week-column the month
actually starts, not evenly spaced. For a 14-week JPMC timeline starting
2026-07-27, compute each month's first Monday-of-week-column position from the
literal kickoff date and place the label there (e.g. Jul 26 at week1, Aug 26 at
week~2, Sep 26 at week~6, Oct 26 at week~10, Nov 26 at week~14/go-live).

### 5d. The bar-drawing mechanism (belt-and-braces: cell value + hidden number format + conditional formatting)

Every week-column cell in a task row (e.g. `L6` = week1 of row6's task) carries:

```
L6: =IF(AND(L$4>=$I6,L$4<=$J6),1,"")
```
i.e. the cell evaluates to `1` if that week-column's header number (row 4) falls
within the row's Live-start/Live-end window, else empty string. This formula is
identical in shape across every week-column/row-pair (just the column letter and
row number change), spanning `L6:BT44`.

The cell's **number format is `;;;`** (three semicolons — the Excel "hide
everything" custom format: no positive/negative/zero/text sections render
anything), so even though the cell literally contains the value `1`, nothing is
displayed as text — the visible "bar" comes entirely from fill colour.

A **conditional-formatting rule** duplicates the exact same boolean test as an
`expression`-type CF rule and applies a solid fill when true:

```
CF range L6:BT6, rule formula: AND(L$4>=$I6,L$4<=$J6), dxf fill: solid ARGB FF2E7D32
```
(one CF rule per contiguous row-range on the sheet — parent rows get their own
range, e.g. `L6:BT6`, and each row's child-step block gets a combined range,
e.g. `L7:BT8` for a 2-row child group). **The CF fill colour is what the user
actually sees as the bar** — the cell-value/number-format layer is redundant
(a belt-and-braces legacy/print-safety mechanism, confirmed by cross-checking
that CF fill and cell truthiness always co-occur). A rebuild can implement the
bar purely via one CF rule per row-range, `expression` type, testing
`AND(<col>$4>=$I<row>,<col>$4<=$J<row>)` against that row's own Live-start/
Live-end cells, no separate cell-value formula strictly required — but
replicating the full belt-and-braces (cell formula `=IF(AND(...),1,"")` +
`;;;` number format + CF rule) matches the source's own technique exactly and
is recommended for fidelity.

### 5e. Bar colours — IMPORTANT: 'Gantt - Revised' uses ONE colour for ALL bars, NOT the legend's 4-colour Side scheme

The legend text (row 2) describes: "Bar colours: SEGRO red · HCL dark blue ·
Azure mid blue · Third-party purple · corrected bars green (proposed, not
committed)." This is the full legend shared conceptually with the As-Drawn
sheet, but **on 'Gantt - Revised' specifically, every single row's CF fill is
green** regardless of the row's `Side` value (SEGRO/HCL/Azure/ThirdParty all
render identically):

- Parent-task rows: solid `FF2E7D32` (dark green)
- Child-step rows: solid `FFA1C5A3` (pale green)

This is a deliberate visual signal ("these are all proposed/recommended
changes, not committed — hence uniformly green") and must NOT be confused with
the 'Gantt - POAP As Drawn' sheet, which DOES use genuine side-based 4-colour
coding (confirmed by inspecting its CF rules): `FFCC171E`/`FFE8979A` (red/pale
red pairs = SEGRO), `FF1F3864`/`FF9AA5B9` (navy/pale navy = HCL),
`FF2E75B6`/`FFA1C1DE` (mid-blue/pale-blue = Azure), `FF7030A0`/`FFBFA2D4`
(purple/pale-purple = Third-party) — alternating darker-for-parent /
lighter-for-child within each side, exactly as the legend describes. **For the
JPMC rebuild's "Revised/Recommended" gantt, replicate the Revised sheet's
convention: everything green (or a single "this is proposed" accent colour),
NOT the 4-colour side scheme** — that side-based scheme belongs only to a
committed/as-drawn baseline view, which the task said to only briefly note
(see 5g) and not build in detail.

### 5f. Milestone/marker row (row 5) and in-row markers

Row 5 (`B5='Go-live range (F1)'`) renders special glyphs per week-column via:

```
L5: =IF(L$4=$BX$4,"▲", IF(L$4=$BY$4,"◁", IF(L$4=$BZ$4,"▷",
      IF(AND(L$4>$BY$4,L$4<$BZ$4),"·", ""))))
```
- `▲` = nominal go-live week (`BX4 = 31+SlipWeeks`)
- `◁` = range-band start (`BY4 = 29+SlipWeeks`)
- `▷` = range-band end (`BZ4 = 33+SlipWeeks`)
- `·` = weeks strictly between the band start/end (the "uncertainty window")
- These are literal displayed glyph characters typed into the cell (not CF —
  this row has NO number-format hiding and NO CF fill; the glyph itself is the
  visible content), so this row's cells are NOT hidden/coloured like the bar
  rows — they show the character directly, left as default black text.

Additionally, individual task-row week-cells occasionally get a **literal
override glyph** instead of the `1`/`""` bar value, layered into the same
`IF(...)` chain seen in row 5's pattern extended into task rows for special
calendar markers:
- `◆` = "drawn phase gate" (e.g. `P5: =IF(...,"◆")`)
- `!` = Bank holiday (e.g. `S5`)
- `✕` = Christmas (e.g. `AI5`)

These four glyphs (`▲ ◁ ▷ ✕ ! ◆` plus `·`) are the full milestone/marker
vocabulary. For the JPMC 14-week rebuild: place `▲` at the nominal go-live week
column, `◁`/`▷` two weeks either side (or the equivalent proportional band),
`✕`/`!` at any Christmas/bank-holiday week-columns that fall inside the new
2026-07-27→2026-11-01 window (none do, since the window is entirely within
Jul-Nov; omit if no UK/US holiday falls in range, or add US Labor Day/UK August
bank holiday if desired), and `◆` at any drawn phase-gate weeks the rebuild
defines.

### 5g. Column BU ("Slip" clip-flag) and BV ("Basis / source")

```
BU6: =IF(IF(G6+H6-1<=29,MAX(ROUND((G6+H6-1)*(29+SlipWeeks)/29,0),$I6),G6+H6-1+SlipWeeks)>61,"▶ clipped","")
```
Recomputes the *unclamped* Live-end (without the `MIN(61,...)` wrapper) and
flags `"▶ clipped"` if it would exceed the last visible week column — i.e. this
warns the user that a large slip has pushed a bar off the printed grid edge.
Column BV holds a free-text basis/rationale note per row (e.g. explaining why a
task was pulled forward, referencing the finding number). Both are non-visual
metadata columns, straightforward to replicate as-is (adjust the `61` cap and
`29`/`SlipWeeks` constants to match the new nominal span).

### 5h. Brief note on 'Gantt - POAP As Drawn' (hidden sheet — NOT to be built in detail)

This is the ORIGINAL (pre-review) as-drawn baseline schedule, `sheet_state=hidden`,
dimensions `A1:BW353` (353 rows — vastly more granular than the 44-row Revised
sheet, since it contains the full un-collapsed step decomposition for every
phase). It uses the same week-column-per-column bar mechanism (CF `expression`
rules testing `AND(K$4>=$I<row>,K$4<=$J<row>)`, columns K:BS instead of L:BT
since it has one extra leading metadata column) but with genuine 4-colour
side-based fills (§5e) rather than uniform green, and its own internal Live-
start/Live-end formulas follow the identical proportional-stretch/additive-shift
transform pattern (§5b) with a nominal core span that also resolves to 29 weeks
pre-slip. It is the "committed/legacy" schedule that the Revised sheet's
findings (F1/F2/F10/F16/F17/F19/F23/F26) were written against. Do not
reconstruct this sheet's full 353-row detail for the JPMC rebuild — a single
"as-drawn baseline" reference sheet (if wanted at all) can be a much smaller
placeholder; the Revised sheet (§5a-5g) is the one requiring full mechanical
fidelity.

## 6. Charts, conditional formatting summary, sheet order/hidden/tab-colours

### 6a. Charts (all 4 live on 'Overview'; no other sheet has a chart)

| # | Type | Anchor | Series (values) | Categories |
|---|------|--------|------------------|------------|
| 1 | BarChart (stacked, waterfall) | ~row12, cols H-N | `Overview!$I$13:$I$22` (base, no-fill), `Overview!$J$13:$J$22` (delta, filled) | `Overview!$H$13:$H$22` |
| 2 | LineChart (cumulative spend) | ~row45, cols A-G | `Overview!$B$47:$B$62`, `Overview!$C$47:$C$62` | `Overview!$A$47:$A$62` |
| 3 | BarChart (clustered, phase-by-side) | ~row63, cols H-N | 4 series: `Overview!$B$65:$B$70`, `$C$65:$C$70`, `$D$65:$D$70`, `$E$65:$E$70` | `Overview!$F$13:$F$18` |
| 4 | DoughnutChart | ~row71, cols H-N | `Overview!$B$73:$B$76` | `Overview!$A$73:$A$76` |

Charts 1+2 implement the waterfall/cumulative technique from §4h (invisible
base column + visible block column, or running-total line). Chart 3 is a
plain side-by-side phase comparison; chart 4 a simple cost-split doughnut. No
print areas are set on any sheet in the workbook.

### 6b. Conditional formatting summary (per sheet)

| Sheet | Rule count (approx) | Rule type | Formula pattern | Fill colour(s) |
|---|---|---|---|---|
| Gantt - Revised | ~40 (one range per parent row / child-step block, `L:BT`) | `expression` | `AND(L$4>=$I<row>,L$4<=$J<row>)` | **Uniform green only**: `FF2E7D32` (parent), `FFA1C5A3` (child) — see §5e, NOT side-coloured |
| Gantt - POAP As Drawn | ~170 (one per task/step row, `K:BS`) | `expression` | `AND(K$4>=$I<row>,K$4<=$J<row>)` | **Genuine 4-side coding** (see §5e/§5h): SEGRO `FFCC171E`/`FFE8979A`, HCL `FF1F3864`/`FF9AA5B9`, Azure `FF2E75B6`/`FFA1C1DE`, ThirdParty `FF7030A0`/`FFBFA2D4` |
| Control Panel | 0 | — | — | (input cells rely on static amber fill, §2, not CF) |
| Overview, Rates, POAP Build Cost, 5yr Run Cost, R1 Pilot, Agents, Team Model, Benchmarks | 0 | — | — | no CF on any of these; all colour is static fill/font (subtotal rows, status-label font, header fills — see §3) |

The dangling reference in §5a ("drives bar colour... see 5d") is resolved by
§5e/§5h above: Revised is uniformly green; only As-Drawn is genuinely
side-coloured. A JPMC "Revised/Recommended" rebuild should replicate the
**uniform-accent-colour** convention, not the 4-side scheme.

### 6c. Sheet order, hidden state, tab colours, dimensions

| # | Sheet name | State | Tab colour (theme/tint) | Dimensions |
|---|---|---|---|---|
| 1 | Overview | visible | theme3, tint 0.7999816888943144 | A1:K83 |
| 2 | Gantt - POAP As Drawn | **hidden** | none | A1:BW353 |
| 3 | Gantt - Revised | visible | theme6, tint 0.0 | A1:BZ44 |
| 4 | Control Panel>>> | visible | theme9, tint 0.0 | A1:D35 |
| 5 | Rates | visible | none | A1:F133 |
| 6 | POAP Build Cost | visible | none | A1:L111 |
| 7 | 5yr Run Cost | visible | none | A1:M76 |
| 8 | R1 Pilot | visible | none | A1:K19 |
| 9 | Agents | visible | none | A1:G14 |
| 10 | Team Model | visible | none | A1:F42 |
| 11 | Benchmarks | visible (tabSelected=True, i.e. the sheet active when last saved) | none | A1:F21 |

Note the workbook order interleaves the two Gantt sheets (positions 2-3)
immediately after Overview and before Control Panel — i.e. the schedule view
is presented ahead of the input/scenario controls and cost sheets. Only 3
sheets carry a tab colour (Overview, Gantt-Revised, Control Panel), which the
JPMC rebuild can either keep (resolve theme+tint to hex once, or pick fresh
equivalent accent colours) or drop tab colouring entirely without loss of
mechanics — it is cosmetic wayfinding only. No print areas are set anywhere
in the workbook.
