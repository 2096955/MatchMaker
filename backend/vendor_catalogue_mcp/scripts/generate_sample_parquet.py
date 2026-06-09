"""Generate a synthetic LSEG catalogue parquet under synthetic/catalogue/.

Stand-in for the real LSEG metadata API output (34 parquet files) until JPMC
hands over the actual schema. Twenty-nine rows because that matches the
"29-product set" called out in the contract docstring. Shape is deliberately
LSEG-ish: typed columns drive matching, raw_attributes_json captures everything
that hasn't been promoted yet.

Schema (placeholder — tighten once the real 34-file schema lands):
  vendor_product_ref : string   (native key)
  title              : string
  description        : string
  theme              : string
  keywords_json      : string   (JSON list — parquet-friendly)
  asset_class        : string
  identifiers_json   : string   (JSON dict — ISIN/CUSIP/RIC etc.)
  raw_attributes_json: string   (JSON dict — vendor-native catch-all)
  snapshot_id        : string   ("lseg-YYYY-MM-DD")
  ingested_at        : timestamp[us, UTC]
  version            : int32
  created_at         : timestamp[us, UTC]  (drives delta ADDED)
  modified_at        : timestamp[us, UTC]  (drives delta UPDATED + modified_since)

Run:
  python -m vendor_catalogue_mcp.scripts.generate_sample_parquet
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

OUT_DIR = Path(__file__).resolve().parent.parent / "synthetic" / "catalogue"
SNAPSHOT_ID = "lseg-2026-05-19"
INGESTED_AT = datetime(2026, 5, 19, 6, 0, 0, tzinfo=timezone.utc)
BASE_DAY = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)


# 29 placeholder LSEG products. Themes/asset-classes are real LSEG taxonomy
# terms; product names are synthetic but evocative of LSEG's actual catalogue.
_PRODUCTS = [
    ("LSEG-IBES-EST-001", "I/B/E/S Estimates — Global Equities",
     "Consensus analyst estimates across global equities.", "Investment Data", "Equities",
     ["estimates", "consensus", "analyst", "ibes"],
     {"ric_pattern": "<ticker>.<exch>"}),
    ("LSEG-WS-FUND-002", "Worldscope Fundamentals",
     "Standardised global company fundamentals.", "Investment Data", "Equities",
     ["fundamentals", "financials", "worldscope"], {}),
    ("LSEG-PRICE-EOD-003", "End-of-Day Pricing — Cross-Asset",
     "EOD prices across equities, FI and FX.", "Pricing & Reference", "Cross-Asset",
     ["eod", "pricing", "reference"], {}),
    ("LSEG-CORP-ACTIONS-004", "Corporate Actions Service",
     "Mandatory and voluntary corporate action events.", "Reference Data", "Equities",
     ["corporate-actions", "events"], {}),
    ("LSEG-ESG-SCORES-005", "ESG Scores",
     "Company-level ESG scores and pillar breakdowns.", "ESG", "Equities",
     ["esg", "scores", "sustainability"], {}),
    ("LSEG-MUNI-REF-006", "Municipal Bond Reference",
     "US municipal bond reference data.", "Reference Data", "Fixed Income",
     ["municipal", "bonds", "reference"], {}),
    ("LSEG-FX-TICK-007", "FX Tick History",
     "Tick-by-tick FX market data.", "Pricing & Reference", "FX",
     ["fx", "tick", "history"], {}),
    ("LSEG-IDX-CONST-008", "Index Constituents",
     "Index membership and weights across global benchmarks.", "Indices", "Equities",
     ["index", "constituents", "weights"], {}),
    ("LSEG-DEAL-CONTRIB-009", "Deals Contributed Pricing",
     "Contributed pricing for OTC fixed income deals.", "Pricing & Reference", "Fixed Income",
     ["deals", "contributed", "otc"], {}),
    ("LSEG-FUND-PERF-010", "Fund Performance",
     "Mutual fund and ETF performance series.", "Investment Data", "Funds",
     ["funds", "performance", "etf"], {}),
    ("LSEG-OWN-INST-011", "Institutional Ownership",
     "Reported institutional holdings by issuer.", "Ownership", "Equities",
     ["ownership", "institutional", "holdings"], {}),
    ("LSEG-ECON-IND-012", "Economic Indicators",
     "Macroeconomic indicators across regions.", "Economics", "Macro",
     ["macro", "indicators", "gdp", "cpi"], {}),
    ("LSEG-CRED-RAT-013", "Credit Ratings",
     "Issuer and issue-level credit ratings history.", "Reference Data", "Fixed Income",
     ["ratings", "credit", "issuer"], {}),
    ("LSEG-DERIV-REF-014", "Derivatives Reference",
     "Listed derivatives reference data.", "Reference Data", "Derivatives",
     ["derivatives", "options", "futures"], {}),
    ("LSEG-NEWS-RT-015", "Reuters News Real-Time",
     "Real-time newswire feed.", "News & Research", "Cross-Asset",
     ["news", "newswire", "realtime"], {}),
    ("LSEG-ENT-MASTER-016", "Entity Master",
     "Legal entity reference (LEI-linked).", "Reference Data", "Cross-Asset",
     ["entity", "lei", "master"], {}),
    ("LSEG-SUPPLY-CHAIN-017", "Supply Chain Relationships",
     "Inferred company supply-chain relationships.", "Investment Data", "Equities",
     ["supply-chain", "relationships"], {}),
    ("LSEG-STARMINE-018", "StarMine Quantitative Models",
     "Quant analytics: SmartEstimate, Earnings Quality, etc.", "Investment Data", "Equities",
     ["starmine", "quant", "smartestimate"], {}),
    ("LSEG-FILINGS-019", "Regulatory Filings",
     "Company regulatory filings store.", "Reference Data", "Equities",
     ["filings", "edgar", "regulatory"], {}),
    ("LSEG-SHRT-INT-020", "Short Interest",
     "Reported short interest by issuer.", "Investment Data", "Equities",
     ["short-interest", "positioning"], {}),
    ("LSEG-OPT-CHAIN-021", "Options Chain",
     "Listed equity options chains.", "Pricing & Reference", "Derivatives",
     ["options", "chains"], {}),
    ("LSEG-FI-ANALYTICS-022", "Fixed Income Analytics",
     "Yield, duration, OAS analytics on fixed-income securities.", "Investment Data", "Fixed Income",
     ["fi-analytics", "yield", "duration", "oas"], {}),
    ("LSEG-COMM-PX-023", "Commodities Pricing",
     "Spot and forward commodity prices.", "Pricing & Reference", "Commodities",
     ["commodities", "spot", "forward"], {}),
    ("LSEG-TRANSCRIPTS-024", "Earnings Call Transcripts",
     "Searchable earnings call transcripts.", "News & Research", "Equities",
     ["transcripts", "earnings", "nlp"], {}),
    ("LSEG-PRIVATE-CO-025", "Private Company Data",
     "Reference and financials for private companies.", "Investment Data", "Equities",
     ["private", "venture"], {}),
    ("LSEG-SYM-XREF-026", "Symbology Cross-Reference",
     "RIC ↔ ISIN ↔ CUSIP ↔ SEDOL cross-reference.", "Reference Data", "Cross-Asset",
     ["symbology", "xref", "ric", "isin"], {}),
    ("LSEG-CB-FACTS-027", "Convertible Bond Facts",
     "Reference and terms for convertible bonds.", "Reference Data", "Fixed Income",
     ["convertibles", "terms"], {}),
    ("LSEG-INSIDER-028", "Insider Transactions",
     "Reported insider transactions by issuer.", "Ownership", "Equities",
     ["insider", "transactions"], {}),
    ("LSEG-CARBON-029", "Carbon Emissions Data",
     "Company-level carbon emissions disclosures and estimates.", "ESG", "Equities",
     ["carbon", "emissions", "ghg"], {}),
]


def _identifiers_for(idx: int, vpr: str, asset_class: str) -> dict[str, str]:
    """Mint plausible identifiers — synthetic but pattern-correct so the
    upstream validators (ISIN regex, ccy ISO-3) don't choke on the fixture."""
    base = {"vendor_native": vpr}
    if asset_class in ("Equities", "Funds"):
        base["isin"] = f"US{idx:010d}"[:12]
        base["cusip"] = f"{idx:09d}"[:9]
        base["ric"] = f"PROD{idx:03d}.OQ"
    elif asset_class == "Fixed Income":
        base["isin"] = f"XS{idx:010d}"[:12]
        base["currency"] = "USD"
    elif asset_class == "FX":
        base["pair"] = "EURUSD"
    elif asset_class == "Commodities":
        base["bbg_ticker"] = f"PROD{idx} Comdty"
    return base


def _row(idx: int, p: tuple) -> dict:
    vpr, title, desc, theme, asset_class, keywords, extra_raw = p

    # Stagger created_at across the 29 products; flip a subset to modified_at
    # later than created_at so delta sees both ADDED and UPDATED slices.
    created = BASE_DAY + timedelta(days=idx)
    modified = created + (timedelta(days=14) if idx % 5 == 0 else timedelta(0))

    raw = {
        "lseg_internal_id": f"LSEG-INT-{idx:05d}",
        "delivery_channel": "metadata-api",
        "license_class": "subscription",
        **extra_raw,
    }

    return {
        "vendor_product_ref": vpr,
        "title": title,
        "description": desc,
        "theme": theme,
        "keywords_json": json.dumps(keywords),
        "asset_class": asset_class,
        "identifiers_json": json.dumps(_identifiers_for(idx, vpr, asset_class)),
        "raw_attributes_json": json.dumps(raw),
        "snapshot_id": SNAPSHOT_ID,
        "ingested_at": INGESTED_AT,
        "version": 1,
        "created_at": created,
        "modified_at": modified,
    }


# Two products that have been REMOVED from the catalogue since the snapshot
# baseline — drives the ChangeType.REMOVED path in get_delta.
_TOMBSTONES = [
    {
        "vendor_product_ref": "LSEG-RETIRED-101",
        "title": "Legacy Tick History (retired)",
        "removed_at": datetime(2026, 5, 7, 9, 0, 0, tzinfo=timezone.utc),
    },
    {
        "vendor_product_ref": "LSEG-RETIRED-102",
        "title": "Pre-MIFID Reference Stub (retired)",
        "removed_at": datetime(2026, 5, 8, 9, 0, 0, tzinfo=timezone.utc),
    },
]


def main() -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = [_row(i, p) for i, p in enumerate(_PRODUCTS, start=1)]
    df = pd.DataFrame(rows)
    catalogue_out = OUT_DIR / "lseg_catalogue.parquet"
    df.to_parquet(catalogue_out, engine="pyarrow", index=False)
    print(f"Wrote {len(df)} catalogue rows  -> {catalogue_out}")

    tomb_df = pd.DataFrame(_TOMBSTONES)
    tomb_out = OUT_DIR / "lseg_tombstones.parquet"
    tomb_df.to_parquet(tomb_out, engine="pyarrow", index=False)
    print(f"Wrote {len(tomb_df)} tombstone rows -> {tomb_out}")

    return catalogue_out, tomb_out


if __name__ == "__main__":
    main()
