# Brief: Build JPMC_SCUDO_POAP_TCO_v1.xlsx

Build a JPMC TCO workbook from scratch with openpyxl. Working dir: /Users/anthonylui/MatchMaker/MatchMaker/jpmc-costings/. Output: JPMC_SCUDO_POAP_TCO_v1.xlsx in that dir.

## Read first (in order)

1. JPMC_TCO_design.md - sheet plan, drivers, defaults, formula patterns. The contract; follow exactly.
2. SEGRO_structure_spec.md - mechanisms to replicate (details in section below).
3. research/rates.json - 112 verified USD us-east-1 rates for the Rates sheet.
4. research/llm_call_model.md - code-verified token/call numbers + three-scenario framing.
5. research/arch_infra_persistence.md - persistence quantities + extra repo-real components.

## Hard rules

- ZERO SEGRO/HCL/Azure/GBP content anywhere. USD only. Scrub targets: SEGRO, HCL, Azure, GBP, pound sign, uksouth.
- Every computed cell is an Excel FORMULA referencing named ranges / Rates cells - never a Python-computed constant. Assumption quantities are hardcoded inputs (blue font; amber fill where user-editable).
- Number formats: "$#,##0;($#,##0);-" for money (zeros show as dash); "$#,##0.0000" variants for sub-dollar unit rates; 0.0% for percents; header-like years/dates as text. Negatives in parentheses.
- Colours: blue font = hardcoded input, black = formula, green = cross-sheet link, bold red FFCC171E = status labels, amber FFFFF2CC fill = editable inputs.
- Every cost row: Status column (VERIFIED/INDICATIVE/ASSUMPTION/DERIVED/RATE_MISSING) + Basis column citing source + as-of date. RATE_MISSING rows visible at $0 with "un-costed, not free" note. PointRate defaults 0 so build cost shows visible zeros BY DESIGN.
- Named ranges exactly as the design doc's Control Panel table; data validations per its Validation column.
- Rates sheet: include every meter any formula references, plus the other verified rates grouped by service; each row keeps its source_url + as-of note from rates.json.

## SEGRO mechanisms to replicate (mechanics only - content NEVER copied)

- Scenario banner: A2 on each major sheet = concatenated ampersand-chain of named cells with TEXT() wrapping numerics (spec 4i).
- SKU zero-gate (4g): token lines nest IF(ModelSKU=...) so only the selected SKU's rate prices; others contribute 0, never error.
- Waterfall (4h): Overview cost waterfall via invisible-base + visible-block helper columns feeding a stacked bar chart.
- Gantt (5a-5f) with core span 14, weeks 1-14 one column each starting at column L (allow columns to week 18 for slip headroom):
  - Parent rows: Live start =MIN(18,IF(G<=14,ROUND((G-1)*(14+SlipWeeks)/14,0)+1,G+SlipWeeks)); Live end analogous with MAX clamp to Live start.
  - Child rows: MEDIAN interpolation within the parent window (spec 5b formula unchanged).
  - Bars: cell =IF(AND(L$4>=$I6,L$4<=$J6),1,"") + number format ;;; + CF expression rule with the same test, solid fill FF2E7D32. One CF rule per row-range.
  - Row 3 month labels at true month boundaries for kickoff w/c 2026-07-27 (Jul wk1, Aug wk2, Sep wk6, Oct wk10, Nov wk14).
  - Milestones: kickoff, infra-ready, first-delivery-E2E, HITL live, go-live 2026-11-01.
  - Lanes = Pods A-E from the design doc (parallel after a 2-week foundation), NOT sequential phases.
- Amber inputs on Control Panel column B; freeze panes A3 there, L6 on Gantt.

## Build order and verification (MANDATORY)

1. Write ONE build script jpmc-costings/build_workbook.py constructing the whole workbook, then run it. Keep the script organized one function per sheet. Concise code, no prints beyond a final summary line.
2. Recalculate with LibreOffice via the xlsx skill's recalc script: run `python3 /Users/anthonylui/.claude/skills/xlsx/recalc.py JPMC_SCUDO_POAP_TCO_v1.xlsx 120` from the jpmc-costings dir (if the path differs, locate recalc.py under ~/.claude/skills/xlsx/). The JSON must report status success / 0 errors. If errors_found: fix the build script and re-run until clean. NEVER hand-patch the xlsx.
3. Scrub check: with openpyxl iterate every cell of every sheet (values AND formulas) and grep case-insensitively for: segro, hcl, azure, gbp, uksouth. Must be 0 hits.
4. Sanity-check 5 spot values with data_only=True after recalc (e.g. ItemsPerMonth=9890, Aurora monthly = 1.038*730 approx 758, the Opus token line matches the LIKELY scenario in llm_call_model.md, 12mo total = SUM of month columns, Pod Build total points = sum of client points).
5. Final message: report recalc JSON status, scrub result, the 5 spot-check values, and the workbook's headline Monthly TCO + 12mo total at defaults. Do not claim production readiness; say "ready for review".

## Reporting discipline

Work incrementally: write the build script in sections (Write skeleton, then Edits per sheet function) to keep each response small. Frequent small tool calls; no long uninterrupted output. Do not invoke any skills.
