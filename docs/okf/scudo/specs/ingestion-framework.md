---
type: Spec
title: Ingestion Framework Spec (ETL §1–11)
description: Format-agnostic ETL ingestion spec; SCUDO pipeline diagrams live in architecture/,
  not here.
tags:
- spec
- ingestion
staleness: current
timestamp: '2026-08-17T09:02:03Z'
---

# Ingestion Framework — Technical Specification

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Class Design](#3-class-design)
4. [Trigger Mechanisms](#4-trigger-mechanisms)
5. [Column Comparison and Validation Logic](#5-column-comparison-and-validation-logic)
6. [File Processing Rules](#6-file-processing-rules)
7. [ETL Run Log](#7-etl-run-log)
8. [File Format Support](#8-file-format-support)
9. [Database Schema Changes](#9-database-schema-changes)
10. [UI Reports Section](#10-ui-reports-section)
11. [Error Handling and Edge Cases](#11-error-handling-and-edge-cases)

---

## 1. Overview

The ingestion framework reads data files from a landing folder, validates them against the registered dataset metadata, loads records into the target ingestion table, and logs the outcome of every run in `etl_run_log`.

Key principles:
- **One physical file is processed exactly once.** A file that has already been loaded is never re-loaded.
- **Schema drift is not a blocker.** Extra or missing columns are recorded as warnings; processing continues on the known columns.
- **All outcomes are observable.** Every run, success or failure, produces a row in `etl_run_log` with enough context for self-service debugging.
- **No hard-coded format logic at the call site.** The caller invokes the base class; format-specific behaviour is encapsulated in derived classes.

---

## 2. Architecture

```
Caller (API endpoint / CLI / scheduler)
         │
         ▼
  IngestionEngine          ← base class, entry point, orchestration
         │
         │  resolves dataset metadata from DB
         │  selects the correct reader
         │
         ├── CsvIngester
         ├── TxtIngester   (configurable delimiter)
         ├── JsonIngester
         ├── XlsxIngester  (configurable sheet name)
         ├── XmlIngester
         └── ParquetIngester
```

The base class (`IngestionEngine`) owns:
- Dataset resolution (from DB via `dataset_id`)
- File discovery and ordering
- Column comparison
- Target table load
- ETL run log writing
- File move to processed folder

Derived classes own **only** the file reading logic: they receive a file path and return a `pandas.DataFrame`.

---

## 3. Class Design

### 3.1 Base Class — `IngestionEngine`

```python
class IngestionEngine:
    """
    Orchestrates a full ingest run for one dataset.
    Subclasses implement read_file() for their specific format.
    """

    def __init__(self, dataset_id: int):
        self.dataset_id = dataset_id
        self.meta = None          # populated by _load_metadata()
        self.run_log_id = None    # set after etl_run_log row is inserted

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run(self) -> list[dict]:
        """
        Discover files, process each one, return list of run-log summaries.
        Called by the trigger layer (API, CLI, scheduler).
        """
        self._load_metadata()
        files = self._discover_files()

        if not files:
            self._log_run(
                file_path=None,
                run_status='skipped',
                status_notes='No files found matching pattern in landing folder',
                column_status='n/a',
                records_loaded=0,
            )
            return []

        results = []
        for file_path in files:
            results.append(self._process_file(file_path))
        return results

    # ------------------------------------------------------------------ #
    # Abstract — subclasses must implement                                 #
    # ------------------------------------------------------------------ #

    def read_file(self, file_path: str) -> 'pd.DataFrame':
        """
        Read the file at file_path and return a DataFrame.
        Receives all dataset metadata via self.meta for format-specific
        parameters (delimiter, sheet_name, etc.).
        """
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Orchestration — implemented in base class                           #
    # ------------------------------------------------------------------ #

    def _load_metadata(self): ...
    def _discover_files(self) -> list[str]: ...
    def _process_file(self, file_path: str) -> dict: ...
    def _compare_columns(self, file_columns: list[str]) -> dict: ...
    def _load_to_target(self, df, file_path: str) -> int: ...
    def _move_to_processed(self, file_path: str) -> str: ...
    def _log_run(self, **kwargs) -> int: ...
    def _already_processed(self, file_path: str) -> bool: ...
```

### 3.2 Factory Function

```python
def get_ingester(dataset_id: int) -> IngestionEngine:
    """
    Resolve the data_format from metadata and return the correct subclass instance.
    The caller never needs to know which format is involved.
    """
    fmt = _fetch_format(dataset_id)   # lightweight DB lookup
    registry = {
        'csv':     CsvIngester,
        'txt':     TxtIngester,
        'json':    JsonIngester,
        'xlsx':    XlsxIngester,
        'xml':     XmlIngester,
        'parquet': ParquetIngester,
    }
    cls = registry.get(fmt)
    if not cls:
        raise ValueError(f'Unsupported format: {fmt}')
    return cls(dataset_id)
```

### 3.3 Derived Classes

Each derived class only implements `read_file()`.

```python
class CsvIngester(IngestionEngine):
    def read_file(self, file_path):
        return pd.read_csv(file_path)

class TxtIngester(IngestionEngine):
    def read_file(self, file_path):
        delimiter = self.meta.get('delimiter', ',')
        return pd.read_csv(file_path, sep=delimiter)

class JsonIngester(IngestionEngine):
    def read_file(self, file_path):
        return pd.read_json(file_path)

class XlsxIngester(IngestionEngine):
    def read_file(self, file_path):
        sheet = self.meta.get('sheet_name') or 0
        return pd.read_excel(file_path, sheet_name=sheet)

class XmlIngester(IngestionEngine):
    def read_file(self, file_path):
        return pd.read_xml(file_path)

class ParquetIngester(IngestionEngine):
    def read_file(self, file_path):
        return pd.read_parquet(file_path)
```

---

## 4. Trigger Mechanisms

### 4.1 Dataset-ID Based Trigger

The primary trigger. The caller provides only a `dataset_id`; all other parameters are resolved from the metadata database.

**Metadata resolved from DB:**

| Field | Source Table | Purpose |
|---|---|---|
| `data_format` | `tp_dataset` | Which derived class to instantiate |
| `sheet_name` | `tp_dataset` | XLSX only |
| `delimiter` | `tp_dataset` | TXT only |
| `landing_folder` | `tp_dataset` | Where to look for files |
| `file_name_pattern` | `tp_dataset` | Glob pattern to match files (e.g. `equity_prices_*.csv`) |
| `target_table` | `tp_dataset` | Ingestion DB table to write to |
| `dataset_id` | `tp_dataset` | Business key written to every loaded row |
| `provider_id` | `tp_dataset` | Business key written to every loaded row |
| columns | `tp_dataset_col` | Current version column list for comparison |

**Invocation:**

```python
# API endpoint (Flask)
@bp.post('/ingest/<int:dataset_id>')
def trigger_ingest(dataset_id):
    ingester = get_ingester(dataset_id)
    results = ingester.run()
    return jsonify(results), 200

# CLI
python -m ingestion.run --dataset-id 3

# Scheduler (cron / Airflow task)
get_ingester(dataset_id=3).run()
```

### 4.2 Future Trigger Modes (out of scope for v1)

| Mode | Description |
|---|---|
| Provider-level | Run all active datasets for a given `provider_id` |
| Full refresh | Reprocess all files regardless of processed status |
| File-path based | Supply an explicit file path, bypassing discovery |

---

## 5. Column Comparison and Validation Logic

Column comparison happens after the file is read, before any rows are loaded. It compares the columns in the current active version of the dataset (`tp_dataset_col WHERE current_flag='y'`) against the columns found in the file.

### 5.1 Comparison Matrix

| Scenario | Condition | Action | `column_status` | `status_notes` |
|---|---|---|---|---|
| **Exact match** | File columns == dataset columns (order-insensitive) | Load all columns | `match` | — |
| **File has more columns** | File has columns not in dataset | Load known columns only; extra columns ignored | `extra_cols_in_file` | List of extra column names |
| **File is missing columns** | Dataset has columns not in file | Load with NULLs for missing; flag as warning | `missing_cols_in_file` | List of missing column names |
| **Both extra and missing** | Combination of above | Load known intersection; flag both | `col_mismatch` | Lists of both extra and missing |

Comparison is **case-insensitive** and **order-insensitive**. Only the intersection of known columns is loaded.

### 5.2 Column Resolution Pseudocode

```python
def _compare_columns(self, file_columns):
    known = {c['col_name'].lower() for c in self.meta['columns']}
    actual = {c.lower() for c in file_columns}

    extra   = sorted(actual - known)
    missing = sorted(known - actual)
    common  = sorted(known & actual)

    if not extra and not missing:
        status = 'match'
        notes  = None
    elif extra and not missing:
        status = 'extra_cols_in_file'
        notes  = f'Extra: {extra}'
    elif missing and not extra:
        status = 'missing_cols_in_file'
        notes  = f'Missing: {missing}'
    else:
        status = 'col_mismatch'
        notes  = f'Extra: {extra} | Missing: {missing}'

    return {'status': status, 'notes': notes, 'load_columns': common}
```

---

## 6. File Processing Rules

### 6.1 File Discovery

1. Read `landing_folder` and `file_name_pattern` from dataset metadata.
2. Glob-match all files in the landing folder against the pattern.
3. Sort matched files by **file modification date, ascending** (oldest first).
4. Process files one at a time in that order.

```python
def _discover_files(self):
    folder  = self.meta['landing_folder']
    pattern = self.meta['file_name_pattern']
    matches = glob.glob(os.path.join(folder, pattern))
    return sorted(matches, key=os.path.getmtime)
```

### 6.2 Duplicate / Already-Processed Check

Before processing each file:

1. Derive a canonical file identity: **filename + file size + file mtime** (stored as `file_path` in `etl_run_log`).
2. Query `etl_run_log` for a row with the same `file_path` and `run_status = 'success'`.
3. If found → skip; write a new `etl_run_log` row with `run_status = 'skipped'` and `status_notes = 'Already processed: {original_run_date}'`.
4. If not found → proceed with processing.

This handles the case where a file was not moved (move failed) and the job is re-run.

### 6.3 Processing Sequence (per file)

```
1. Check already-processed → skip if yes
2. Read file into DataFrame using derived class read_file()
3. Compare columns → determine load_columns
4. Filter DataFrame to load_columns only
5. Add audit columns to every row:
       load_id      = <new run log id>
       provider_id  = self.meta['provider_id']
       dataset_id   = self.meta['dataset_id']
       load_date    = NOW()
       load_process = 'dataset_id:<id>|file:<filename>'
6. Bulk-insert into target_table (ingestion DB)
7. Write etl_run_log row (run_status = 'success' or 'failed')
8. Move file to <landing_folder>/processed/<YYYYMMDD_HHMMSS>/<filename>
9. If move fails → log warning in status_notes; do NOT revert the load
```

### 6.4 Processed Folder Structure

```
<landing_folder>/
    processed/
        20240102_083000/
            equity_prices_20240102.csv
        20240103_091500/
            equity_prices_20240103.csv
```

Timestamped sub-folder per run prevents name collisions when the same filename arrives on different dates.

### 6.5 Move-Failed Rerun Safety

If a move fails and the user reruns the ingestion:
- Step 1 (already-processed check) finds the `run_status = 'success'` row with the same `file_path`.
- The file is skipped; a new `run_status = 'skipped'` row is logged.
- The move is reattempted as part of the skipped-file handling so the file eventually leaves the landing folder.

---

## 7. ETL Run Log

### 7.1 Table: `etl_run_log` (metadata database)

```sql
CREATE TABLE etl_run_log (
    run_id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    provider_id     INT          NOT NULL,
    dataset_id      INT          NOT NULL,
    run_date        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    run_status      VARCHAR(20)  NOT NULL
                    COMMENT 'success | failed | skipped | partial',
    column_status   VARCHAR(30)  NOT NULL
                    COMMENT 'match | extra_cols_in_file | missing_cols_in_file | col_mismatch | n/a',
    status_notes    TEXT,
    file_path       VARCHAR(1000),
    file_name       VARCHAR(500),
    file_size_bytes BIGINT,
    file_mtime      DATETIME,
    records_found   INT          COMMENT 'rows in the source file',
    records_loaded  INT          COMMENT 'rows successfully inserted',
    records_skipped INT          COMMENT 'rows rejected (type errors, nulls on required cols)',
    target_table    VARCHAR(255),
    dataset_version INT          COMMENT 'tp_dataset.version at time of run',
    error_detail    TEXT         COMMENT 'full exception traceback if run_status=failed',
    created_by      VARCHAR(100) DEFAULT 'system',

    INDEX idx_run_dataset  (dataset_id, run_date),
    INDEX idx_run_provider (provider_id, run_date),
    INDEX idx_run_status   (run_status),
    INDEX idx_run_file     (file_name, run_status)
);
```

### 7.2 `run_status` Values

| Value | Meaning |
|---|---|
| `success` | All records loaded; file moved to processed folder |
| `failed` | Unrecoverable error; no records loaded; file not moved |
| `partial` | Some records loaded, some rejected (type errors etc.); file moved |
| `skipped` | File already processed in a prior run, or no files found |

### 7.3 Sample Rows

| run_id | dataset_id | run_status | column_status | status_notes | file_name | records_loaded |
|---|---|---|---|---|---|---|
| 1 | 3 | success | match | — | equity_prices_20240102.csv | 250 |
| 2 | 3 | success | extra_cols_in_file | Extra: ['low'] | equity_prices_20240103.csv | 250 |
| 3 | 3 | skipped | n/a | Already processed: 2024-01-02 08:30 | equity_prices_20240102.csv | 0 |
| 4 | 3 | failed | n/a | FileNotFoundError: landing folder does not exist | — | 0 |

---

## 8. File Format Support

| Format | Class | Key Parameters | Notes |
|---|---|---|---|
| CSV | `CsvIngester` | — | Auto-detects header row |
| TXT | `TxtIngester` | `delimiter` (stored in `tp_dataset`) | Treated as CSV with a custom separator |
| JSON | `JsonIngester` | — | Top-level array or newline-delimited |
| XLSX | `XlsxIngester` | `sheet_name` (stored in `tp_dataset`) | Defaults to first sheet if not specified |
| XML | `XmlIngester` | — | Uses `pd.read_xml`; repeating element = row |
| Parquet | `ParquetIngester` | — | Schema read from Parquet metadata |

---

## 9. Database Schema Changes

### 9.1 Add columns to `tp_dataset`

```sql
ALTER TABLE tp_dataset
    ADD COLUMN delimiter  VARCHAR(10)  AFTER data_format,
    ADD COLUMN sheet_name VARCHAR(255) AFTER delimiter;
```

> `delimiter` is used by `TxtIngester`. `sheet_name` is used by `XlsxIngester`.
> Both are already resolved in `_load_metadata()` and available via `self.meta`.

### 9.2 New table: `etl_run_log`

See Section 7.1 — add to `init_db.sql`.

---

## 10. UI Reports Section

### 10.1 Navigation

Add a **Reports** item to the main navigation sidebar, below Datasets.

### 10.2 Pages

#### 10.2.1 Ingestion Run Summary (`/reports`)

A filterable table showing one row per `etl_run_log` entry.

**Filters (top bar):**
- Provider (dropdown)
- Dataset (dropdown, filtered by selected provider)
- Status (multi-select: success / failed / skipped / partial)
- Date range (from / to date pickers)

**Columns:**

| Column | Source |
|---|---|
| Run ID | `run_id` |
| Provider | `provider_id` → lookup provider name |
| Dataset | `dataset_id` → lookup dataset name |
| Run Date | `run_date` |
| Status | `run_status` (colour-coded badge) |
| Column Status | `column_status` (badge) |
| File | `file_name` |
| Records Loaded | `records_loaded` |
| Notes | `status_notes` (truncated, expand on click) |
| Actions | Detail link |

**Status badge colours (accessible, not colour-only):**
- `success` — green badge, "✓ Success"
- `failed` — red badge, "✗ Failed"
- `partial` — amber badge, "⚠ Partial"
- `skipped` — grey badge, "— Skipped"

#### 10.2.2 Run Detail (`/reports/<run_id>`)

Full detail view for a single run log row.

Sections:
- **Run Summary** — run_id, status, run_date, created_by
- **Dataset Info** — provider name, dataset name, dataset version at time of run, target_table
- **File Info** — file_path, file_name, file_size, file_mtime
- **Record Counts** — records_found, records_loaded, records_skipped
- **Column Status** — column_status badge + full status_notes (untruncated)
- **Error Detail** — full `error_detail` stacktrace (shown only if run_status = 'failed'; monospace scrollable block)

### 10.3 API Endpoints

```
GET  /api/reports                    list with filter params: provider_id, dataset_id, status, from_date, to_date
GET  /api/reports/<int:run_id>       single run detail
POST /api/ingest/<int:dataset_id>    trigger ingestion and return run log summaries
```

---

## 11. Error Handling and Edge Cases

| Scenario | Behaviour |
|---|---|
| Landing folder does not exist | `run_status = 'failed'`; `error_detail` = path + OS error; no file processed |
| File is unreadable (permissions, corrupt) | `run_status = 'failed'` for that file; continue to next file |
| All columns in file are unknown | `run_status = 'failed'`; `column_status = 'col_mismatch'`; no rows loaded |
| Target table does not exist | `run_status = 'failed'`; `error_detail` includes table name; raised as configuration error |
| File move fails after successful load | Load is kept; `status_notes` includes move error; `run_status = 'partial'`; re-run will detect via already-processed check and reattempt move |
| Dataset metadata not found (bad dataset_id) | Raise immediately with HTTP 404 before any file work begins |
| XLSX sheet not found | `run_status = 'failed'`; `error_detail` lists available sheet names |
| TXT file with no delimiter configured | Default to comma; log warning in `status_notes` |
| Zero-row file | Load metadata columns only (0 data rows); `run_status = 'success'`; `records_loaded = 0` |
| Multiple files on same run | Each file gets its own `etl_run_log` row; a parent-level summary row is NOT created (each row is independently queryable) |

---

For the SCUDO matching-pipeline diagrams, see [architecture diagrams & sources](/architecture/diagrams-and-sources.md).

## Related

- [Confidence bands & provenance (canonical)](/reference/matching-data-provenance.md)
- [Architecture diagrams](/architecture/diagrams-and-sources.md)
