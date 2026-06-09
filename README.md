# Third-Party Data Ingestion Framework

A full-stack application for registering third-party data providers, configuring their datasets, and ingesting their files into a central data store. Built with a Flask REST API backend, a React single-page application frontend, and MySQL for both metadata and ingested data.

---

## Table of Contents

1. [Features](#1-features)
2. [Purpose](#2-purpose)
3. [Architecture Overview](#3-architecture-overview)
4. [Folder Structure](#4-folder-structure)
5. [Database Design](#5-database-design)
6. [Backend — REST API](#6-backend--rest-api)
7. [Ingestion Engine](#7-ingestion-engine)
8. [Column Transformations](#8-column-transformations)
9. [Logging](#9-logging)
10. [Frontend — Web UI](#10-frontend--web-ui)
11. [Supported File Formats](#11-supported-file-formats)
12. [Access Control](#12-access-control)
13. [Setup and Running](#13-setup-and-running)
14. [API Reference](#14-api-reference)
15. [Tests](#15-tests)
16. [Key Design Decisions](#16-key-design-decisions)

---

## 1. Features

### Register a Provider
Add a named third-party data provider (e.g. a market data vendor or rating agency) with a description. Each provider is versioned — edits create a new version while the full history is retained. Providers can be activated or soft-deleted without losing their configuration.

### Configure a Dataset
For each provider, define one or more datasets. A dataset captures:
- **Landing folder** — the directory the provider drops files into.
- **File pattern** — a glob (e.g. `*.csv`, `prices_*.xlsx`) that identifies which files to pick up.
- **File format** — CSV, TXT (delimiter-separated), Excel, JSON, XML, or Parquet.
- **Target table** — the ingestion-database table where loaded rows will be stored.
- **Column schema** — the expected columns (name, type, position). Adding or removing columns creates a new dataset version automatically.

### Define Column Transformations
On top of the column schema, you can attach per-column cleansing rules that are applied automatically at ingest time:
- **Trim** — strip leading and trailing whitespace from string values.
- **Null replace** — convert a sentinel string (e.g. `"N/A"`, `"-"`) to a proper SQL `NULL`.
- **Case** — normalise strings to `upper`, `lower`, or `title` case.
- **Date format** — parse and reformat date strings into a target pattern (e.g. `DD/MM/YYYY → YYYY-MM-DD`).

Rules are ordered and saved per dataset version, so you can tune them without touching code.

### Ingest Data
Trigger ingestion for any registered dataset through the UI or the REST API. The engine:
1. Scans the landing folder for files matching the configured pattern.
2. Skips any file it has already successfully processed (deduplication by file name and size).
3. Reads the file with the appropriate parser (pandas-backed for all six formats).
4. Compares the file's columns against the registered schema and records whether they match, have extras, or are missing columns.
5. Applies column transformations in order.
6. Loads the cleansed rows into the target table, writing `NULL` for any schema column absent from the file.
7. Moves the processed file to a `processed/` sub-folder and records the outcome.

### Monitor Execution Logs
Every ingestion run — whether it succeeds, is skipped, or fails — produces a structured log entry capturing:
- Run timestamp and duration.
- Number of records found, loaded, and skipped.
- Column comparison outcome (`match`, `extra_cols_in_file`, `missing_cols_in_file`, `col_mismatch`).
- Full error traceback if the run failed.
- Status notes explaining why a file was skipped.

The web UI's Reports screen shows a filterable, paginated view of all run logs so operators can spot issues at a glance without querying the database directly.

---

## 2. Purpose

Many business processes depend on data delivered by external third-party providers — market data vendors, rating agencies, news services, and similar sources. Each provider typically delivers files in different formats (CSV, Excel, JSON, XML, Parquet), on different schedules, with different column layouts.

This framework provides:

- A **registry** to define and version-control every provider and dataset configuration.
- A **configurable ingestion engine** that reads files, validates their structure, applies cleansing transformations, and loads data into a target table — without writing any bespoke code per provider.
- A **web interface** so operations teams can manage providers, datasets, and transformations without touching code or databases directly.
- A **run log** capturing the outcome of every ingestion: how many rows were loaded, what column discrepancies were found, and the full error traceback if something went wrong.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (React SPA)                      │
│  Providers · Datasets · Transforms · Ingestion · Reports · Admin│
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP / JSON  (Vite dev proxy → :5000)
┌───────────────────────────▼─────────────────────────────────────┐
│                    Flask REST API  (:5000)                       │
│   /api/providers   /api/datasets   /api/ingest   /api/reports   │
│   /api/roles       /api/users                                   │
└──────────┬──────────────────────────────────────────────────────┘
           │ pymysql
  ┌────────▼──────────┐          ┌──────────────────────────────┐
  │  metadata  (MySQL)│          │    ingestion  (MySQL)        │
  │  tp_provider      │          │  {provider}_{dataset_id}     │
  │  tp_dataset       │          │  (one table per dataset)     │
  │  tp_dataset_col   │          └──────────────────────────────┘
  │  tp_dataset_col   │
  │    _transforms    │
  │  etl_run_log      │
  │  roles / users    │
  └───────────────────┘
```

**Two MySQL databases are used:**

| Database | Purpose |
|---|---|
| `metadata` | Stores provider/dataset definitions, column schemas, transformation rules, run logs, and user/role administration. |
| `ingestion` | Stores the actual data loaded from each provider's files. One table is automatically created per dataset. |

---

## 4. Folder Structure

```
ingestion/
├── start_all.sh              — One command to start backend + frontend
├── backend/
│   ├── app.py                — Flask entry point; registers all blueprints
│   ├── db.py                 — pymysql connection helpers (get_conn, get_ingestion_conn)
│   ├── logger.py             — Centralised logging (AppLogger / FileLogger)
│   ├── init_db.sql           — Full schema DDL + seed data
│   ├── requirements.txt
│   ├── start.sh
│   ├── ingestion/
│   │   ├── engine.py         — IngestionEngine abstract base class (ETL pipeline)
│   │   ├── factory.py        — get_ingester() — resolves engine subclass by format
│   │   └── readers.py        — Format-specific subclasses (CSV, TXT, XLSX, JSON, XML, Parquet)
│   ├── routes/
│   │   ├── providers.py      — /api/providers  CRUD
│   │   ├── datasets.py       — /api/datasets   CRUD + transforms + file upload
│   │   ├── ingest.py         — /api/ingest     trigger + /api/reports  run logs
│   │   └── admin.py          — /api/roles  /api/users
│   ├── logs/
│   │   ├── ui.log            — API-layer events (rotates at 10 MB, 5 backups)
│   │   └── ingestion.log     — ETL pipeline events
│   └── tests/
│       ├── _ingest_helpers.py
│       ├── test_providers.py
│       ├── test_datasets.py
│       ├── test_ingest_csv.py
│       ├── test_ingest_txt.py
│       ├── test_ingest_xlsx.py
│       ├── test_ingest_json.py
│       ├── test_ingest_xml.py
│       ├── test_ingest_parquet.py
│       └── run_all_tests.py
├── frontend/
│   ├── src/
│   │   ├── App.jsx           — React Router routes
│   │   ├── api/index.js      — axios wrapper for all API calls
│   │   ├── components/       — Layout, ConfirmModal
│   │   └── pages/
│   │       ├── providers/    — ProviderList, ProviderForm
│   │       ├── datasets/     — DatasetList, DatasetForm, DatasetTransforms
│   │       ├── admin/        — RoleList, RoleForm, UserList, UserForm
│   │       ├── ingestion/    — IngestionConsole
│   │       └── reports/      — ReportList, ReportDetail
│   └── vite.config.js
├── landing_zone/             — Example landing folders for real providers
│   ├── factset/
│   ├── reuters/
│   └── sp_global/
└── sample_data/              — Sample files used for testing
    └── provider/
        ├── bloomberg/        — CSV equity price files
        ├── factset/          — XLSX company fundamentals
        ├── reuters/          — JSON news sentiment
        └── sp_global/        — XML credit ratings
```

---

## 5. Database Design

### SCD Type-2 Versioning (Providers and Datasets)

Providers and datasets use **Slowly Changing Dimension Type 2** — every edit creates a new row rather than overwriting the existing one. This means the full history of every configuration change is preserved.

Each versioned table carries two keys:

| Key | Column | Description |
|---|---|---|
| Surrogate key | `provider_sid` / `dataset_sid` | Auto-increment. Changes on every insert. Used only as the physical PK. |
| Business key | `provider_id` / `dataset_id` | Stable across all versions. Set once (= first surrogate key). Used by all foreign key relationships. |
| Status flag | `current_flag` | `y` = active version, `n` = superseded, `d` = logically deleted. |

On an **edit**, the current row is set to `current_flag = 'n'` and a new row is inserted with `version + 1` and `current_flag = 'y'`.  
On a **delete**, all version rows for that business key are set to `current_flag = 'd'`.  
Queries always filter on `current_flag = 'y'` to see only the live version.

### Core Metadata Tables

| Table | Description |
|---|---|
| `tp_provider` | One row per version of a provider. Contains name, description, website. |
| `tp_dataset` | One row per version of a dataset. Contains format, file pattern, landing folder, target table name, delimiter (TXT), sheet name (XLSX). |
| `tp_dataset_col` | Column definitions for a dataset. Versioned with the dataset — each schema edit replaces these rows. |
| `tp_dataset_col_transforms` | Per-column transformation rules: trim, null replacement, case conversion, date format conversion. One row per column, replaced wholesale on save. |
| `etl_run_log` | One row per file processed. Records status, row counts, column discrepancy notes, error tracebacks, and file metadata. |
| `roles` | Application roles with granular resource permissions. |
| `users` | Application users; many-to-many with roles via `user_roles`. |

### Ingestion Tables (ingestion database)

When a dataset is created, a physical table is automatically created in the `ingestion` database named `{sanitized_provider_name}_{dataset_id}`. For example, provider "Bloomberg" and dataset id 7 → table `bloomberg_7`.

Each row contains the dataset's declared columns plus five mandatory audit columns appended automatically:

| Audit Column | Description |
|---|---|
| `load_id` | `run_id` from `etl_run_log` — links data rows back to their run. |
| `provider_id` | Business key of the provider. |
| `dataset_id` | Business key of the dataset. |
| `load_date` | UTC timestamp of when the row was inserted. |
| `load_process` | Identifies the dataset and file pattern that produced the row. |

---

## 6. Backend — REST API

The Flask app mounts four blueprints, all prefixed under `/api`.

### Providers — `/api/providers`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/providers` | List all active providers. Optional `?q=` substring search. |
| GET | `/providers/<id>` | Get a single provider by business key. |
| POST | `/providers` | Create a new provider. Required: `provider_name`. |
| PUT | `/providers/<id>` | Update a provider (creates a new SCD-2 version). |
| DELETE | `/providers/<id>` | Logically delete provider and all its datasets and columns. |

**Validations:** Duplicate names are rejected (case-insensitive). The `provider_name` field is required on create and update.

### Datasets — `/api/datasets`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/datasets` | List all active datasets with columns. Optional `?q=` search and `?provider_id=` filter. |
| GET | `/datasets/<id>` | Get a single dataset with its column list. |
| POST | `/datasets` | Create a dataset and provision its physical ingestion table. |
| PUT | `/datasets/<id>` | Update a dataset (SCD-2 version). New columns are added to the ingestion table via `ALTER TABLE`; existing columns are never dropped. |
| DELETE | `/datasets/<id>` | Logically delete dataset and all its columns. Physical ingestion table is preserved. |
| POST | `/datasets/upload` | Upload a sample file to infer column names and types. Returns column list and sample rows. File is deleted after processing. |
| GET | `/datasets/<id>/transforms` | Retrieve transformation rules merged with the column list. |
| PUT | `/datasets/<id>/transforms` | Replace all transformation rules for a dataset. |

**Validations on create/update:**
- `dataset_name` and `provider_id` are required.
- The `landing_folder` path must exist on the server filesystem.
- The same landing folder cannot be assigned to datasets from different providers.
- The same `file_name_pattern` cannot be used by two datasets within the same provider.
- Duplicate dataset names within the same provider are rejected.
- Sample file upload enforces that the file extension matches the selected data format.

### Ingestion — `/api/ingest` and `/api/reports`

| Method | Endpoint | Description |
|---|---|---|
| POST | `/ingest/<dataset_id>` | Trigger a synchronous ingestion run for the dataset. Returns the list of run-log entries (one per file processed). |
| GET | `/reports` | Query run logs with optional filters: `provider_id`, `dataset_id`, `status`, `from_date`, `to_date`. Returns up to 500 rows. |
| GET | `/reports/<run_id>` | Fetch a single run-log entry. |

### Administration — `/api/roles` and `/api/users`

| Method | Endpoint | Description |
|---|---|---|
| GET/POST | `/roles` | List all roles / create a role with privileges. |
| GET/PUT/DELETE | `/roles/<id>` | Get, update, or delete a role. |
| GET/POST | `/users` | List all users / create a user. |
| GET/PUT/DELETE | `/users/<id>` | Get, update, or delete a user. |

Password policy: minimum 8 characters, at least one uppercase letter, at least one digit.

---

## 7. Ingestion Engine

The ingestion engine lives in `backend/ingestion/`. It is structured as an abstract base class (`IngestionEngine`) with format-specific subclasses that implement only the `read_file()` method.

### How It Works — Step by Step

When `POST /api/ingest/<dataset_id>` is called, or when `get_ingester(dataset_id).run()` is called directly:

```
1. Load metadata
   Read tp_dataset + tp_dataset_col for the dataset_id.
   Raises ValueError if the dataset is not found or not active.

2. Discover files
   Glob the landing_folder using file_name_pattern.
   Files inside processed/ subdirectory are excluded.
   Results are sorted by modification time (oldest first).

3. For each file found:

   a. Check deduplication
      Query etl_run_log for (dataset_id, file_name, file_size_bytes)
      with run_status = 'success'.
      If found → log as 'skipped', move file to processed/, return.

   b. Create in-progress log stub
      Insert a row in etl_run_log with run_status = 'in_progress'
      so polling clients can detect the active run.

   c. Read file
      Call the subclass read_file() → pandas DataFrame.

   d. Apply transformations
      Call do_transformations(df) — applies trim, null replace,
      case conversion, date format rules from tp_dataset_col_transforms.

   e. Compare columns
      Compare DataFrame columns against tp_dataset_col.
      Four outcomes: match / extra_cols_in_file /
                     missing_cols_in_file / col_mismatch.
      The intersection (known ∩ actual) is the set of columns to load.
      If the intersection is empty, the run is marked 'failed'.

   f. Load to target table
      Bulk-insert the intersection columns into ingestion.{target_table}.
      Missing schema columns are inserted as NULL.
      Rows are inserted one at a time for per-row error isolation.
      Append the five audit columns to every row.

   g. Archive file
      Move file to {landing_folder}/processed/YYYYMMDD_HHMMSS/filename.

   h. Update run log
      Final status = 'success' (all rows loaded, file moved),
                     'partial' (some rows skipped or move failed),
                     'failed'  (no loadable columns or unhandled exception).

4. If no files found:
   Log a single 'skipped' entry with records_found = 0.
```

### Deduplication

A file is considered already processed if a row exists in `etl_run_log` with:
- Same `dataset_id`
- Same `file_name` (basename only)
- Same `file_size_bytes`
- `run_status = 'success'`

Using name + size (not mtime) means a re-delivered identical file is safely skipped, while a file with the same name but different content (different size) is treated as new data.

### Column Comparison Outcomes

| Status | Meaning |
|---|---|
| `match` | File columns exactly equal registered schema columns. |
| `extra_cols_in_file` | File has columns not in the schema. They are silently ignored. |
| `missing_cols_in_file` | Schema has columns absent from the file. They are loaded as NULL. |
| `col_mismatch` | Both extra and missing columns are present. |

### Format-Specific Subclasses

| Class | Format | `read_file()` uses |
|---|---|---|
| `CsvIngester` | `.csv` | `pandas.read_csv()` |
| `TxtIngester` | `.txt` | `pandas.read_csv(sep=delimiter)` — delimiter from dataset config |
| `XlsxIngester` | `.xlsx` | `pandas.read_excel(sheet_name=...)` — sheet name from dataset config |
| `JsonIngester` | `.json` | `pandas.read_json()` |
| `XmlIngester` | `.xml` | `pandas.read_xml()` |
| `ParquetIngester` | `.parquet` | `pandas.read_parquet()` |

To add a new format: implement a subclass in `readers.py` and register it in `factory.py`.

---

## 8. Column Transformations

Each dataset can have per-column transformation rules stored in `tp_dataset_col_transforms`. Transformations are applied after reading the file and before the column comparison step.

The transformations are applied in this order:

```
1. Trim        — strip leading/trailing whitespace from string columns
2. Null replace — substitute a configured literal value where the cell is NULL/empty
3. Case        — convert string columns to upper / lower / sentence case
4. Date format — parse from one strftime pattern and reformat to another
```

### Available Options per Column

| Field | Options | Applies to |
|---|---|---|
| `trim_option` | `trim`, `ltrim`, `rtrim` | string, date, datetime columns |
| `null_replace` | Any free-text value | All column types |
| `case_option` | `upper`, `lower`, `sentence` | string, date, datetime columns |
| `date_fmt_from` | strftime pattern (e.g. `%d/%m/%Y`) | string, date, datetime columns |
| `date_fmt_to` | strftime pattern (e.g. `%Y-%m-%d`) | string, date, datetime columns |

Rules are managed from the **Column Transforms** screen in the UI or via `PUT /api/datasets/<id>/transforms`. Rows where all fields are left blank are not saved — they represent "no transformation required".

---

## 9. Logging

All events are written to rotating log files in `backend/logs/`.

### Two Log Channels

| Logger | File | What gets logged |
|---|---|---|
| `ui_logger` | `logs/ui.log` | All API route activity: requests received, records created/updated/deleted, validation rejections, exceptions. |
| `ingestion_logger` | `logs/ingestion.log` | Full ETL pipeline detail: metadata load, file discovery, column comparison, row-level load counts, file archival, and error tracebacks. |

### Log Format

```
2026-05-28 10:12:34 [INFO    ] Provider created  provider_id=5  name='Acme Corp'
2026-05-28 10:13:01 [WARNING ] Dataset creation rejected — landing folder missing  dataset_id=12  folder='/data/missing'
2026-05-28 10:14:45 [ERROR   ] File processing failed  run_id=88  file='prices.csv'  error='...'
```

### Swapping the Log Destination

The logging system is built around an abstract base class (`AppLogger`) with a single `_emit()` method. The two module-level singletons in `logger.py` are the only things that need changing to redirect logs to a database, cloud logging service, message queue, or SIEM:

```python
# Current — writes to rotating files
ui_logger        = FileLogger('ui',        'ui.log')
ingestion_logger = FileLogger('ingestion', 'ingestion.log')

# Future — replace with any AppLogger subclass, no other code changes needed
ui_logger        = DatabaseLogger('ui')
ingestion_logger = CloudLogger('ingestion')
```

Files rotate at 10 MB and keep 5 backups, capping total disk use at 120 MB across both logs.

---

## 10. Frontend — Web UI

The React SPA (Vite, React Router v6) connects to the Flask API via a `/api` proxy. All pages use the `Layout` shell which provides the navigation sidebar.

### Screens

#### Providers
- **Provider List** — searchable table of all active providers with Edit and Delete actions.
- **Provider Form** — create or edit a provider (name, description, website). The form also shows the version history of the provider.

#### Datasets
- **Dataset List** — searchable, filterable table. Shows format badge, provider name, and action buttons: Edit, Transforms, Trigger Ingestion.
- **Dataset Form** — create or edit a dataset. The form includes:
  - Provider selection, format dropdown, file pattern, landing folder, frequency.
  - **Sample file upload** — drag-and-drop or browse. The upload enforces the selected format (e.g., a `.json` file is rejected when format is set to `csv`). After upload, column names and types are inferred automatically and displayed in an editable grid.
  - Column grid — shows column name, type, and position. Columns can be added manually or imported from a sample file.
- **Column Transforms** — full-page grid showing every column with dropdowns for trim, case, date format, and a free-text null-replacement field. Disabled cells indicate the transformation type does not apply to that column's data type. Saved with a confirmation badge.

#### Ingestion Console
- Displays all active datasets with a **Run** button.
- Shows live results after triggering: number of rows loaded, column status, and any notes.

#### Reports
- **Report List** — filterable run history (by provider, dataset, status, date range).
- **Report Detail** — full detail for a single run: row counts, column comparison notes, file metadata, and the full error traceback if the run failed.

#### Administration
- **Roles** — create and manage roles with granular resource-level permissions (add / modify / delete per resource type).
- **Users** — create and manage users with role assignments. Password policy enforced on create and update.

---

## 11. Supported File Formats

| Format | Extension | Notes |
|---|---|---|
| CSV | `.csv` | Standard comma-separated. Pandas defaults. |
| TXT | `.txt` | Delimiter is configurable per dataset (comma, pipe, tab, etc.). |
| Excel | `.xlsx`, `.xls` | Sheet name is configurable per dataset; defaults to the first sheet. |
| JSON | `.json` | Records-oriented array or pandas-compatible JSON. |
| XML | `.xml` | Parsed by `pandas.read_xml`. Root element contains row elements. |
| Parquet | `.parquet` | Apache Parquet columnar format. Requires `pyarrow`. |

---

## 12. Access Control

Users are assigned one or more **roles**. Each role carries a set of **privileges** — each privilege specifies a `resource_type` (e.g., `provider`, `dataset`, `report`) and three boolean flags: `can_add`, `can_modify`, `can_delete`.

The administration screens allow creating fine-grained roles such as "Read-only analyst" (no add/modify/delete anywhere) or "Data engineer" (full access to providers and datasets, read-only on reports).

---

## 13. Setup and Running

### Prerequisites

- Python 3.10+
- Node.js 18+
- MySQL 8.0+ running locally
- Two MySQL databases: `metadata` and `ingestion`

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `my_sql_user` | `root` | MySQL username |
| `my_sql_password` | *(empty)* | MySQL password |

Set these in your shell profile or in a `.env` file in `backend/`.

### First-time Setup

```bash
# 1. Create databases and schema
mysql -u root -p < backend/init_db.sql

# 2. Install Python dependencies
cd backend
pip install -r requirements.txt

# 3. Install Node dependencies
cd ../frontend
npm install
```

### Running

```bash
# Start both servers together
./start_all.sh

# Or start separately
cd backend && python app.py          # Flask on :5000
cd frontend && npm run dev           # Vite + React on :3000
```

Open `http://localhost:3000` in a browser.

---

## 14. API Reference

### Quick Examples

**Create a provider**
```bash
curl -X POST http://localhost:5000/api/providers \
  -H "Content-Type: application/json" \
  -d '{"provider_name": "Bloomberg", "provider_desc": "Market data"}'
```

**Create a dataset**
```bash
curl -X POST http://localhost:5000/api/datasets \
  -H "Content-Type: application/json" \
  -d '{
    "provider_id": 1,
    "dataset_name": "Equity Prices",
    "data_format": "csv",
    "file_name_pattern": "equity_prices_*.csv",
    "landing_folder": "/data/landing/bloomberg",
    "frequency": "daily",
    "columns": [
      {"col_name": "ticker",     "col_type": "string",  "col_position": 1},
      {"col_name": "price",      "col_type": "decimal", "col_position": 2},
      {"col_name": "trade_date", "col_type": "date",    "col_position": 3}
    ]
  }'
```

**Trigger ingestion**
```bash
curl -X POST http://localhost:5000/api/ingest/1
```

**Query run history**
```bash
curl "http://localhost:5000/api/reports?dataset_id=1&status=success,failed&from_date=2026-01-01"
```

### Run Log Status Values

| Status | Meaning |
|---|---|
| `in_progress` | File is currently being processed. |
| `success` | All rows loaded, file archived. |
| `partial` | Rows loaded but some were skipped, or the file move failed. |
| `failed` | No loadable columns found, or an unhandled exception occurred. |
| `skipped` | File was already processed (dedup match), or no files were found. |

---

## 15. Tests

Tests are standalone Python scripts — no test framework required. Each file can be run individually or all together.

```bash
cd backend/tests

# Run all tests
python run_all_tests.py

# Run a specific area
python test_providers.py
python test_datasets.py
python test_ingest_csv.py
python test_ingest_xlsx.py
# ... etc
```

Each test function calls the same helper methods used by the application (`add_provider()`, `add_dataset()`, `run_ingestion()`, etc.), asserts the result, and cleans up its own data.

### Test Coverage

| File | Area | Scenarios |
|---|---|---|
| `test_providers.py` | Provider registration | Create, duplicate name, missing name, get, update (SCD-2 version), name collision, delete, search |
| `test_datasets.py` | Dataset registration | Create, provider not found, folder missing, duplicate name, shared landing folder, shared pattern, get, update with new columns, column preservation, transforms save/retrieve/replace |
| `test_ingest_csv.py` | CSV ingestion | New file, same file twice (dedup), extra columns, missing columns, empty folder, multiple files, schema extension |
| `test_ingest_txt.py` | TXT ingestion | Pipe delimiter, delimiter mismatch, dedup, extra/missing columns, empty folder |
| `test_ingest_xlsx.py` | Excel ingestion | New file, named sheet, dedup, extra/missing columns, empty folder |
| `test_ingest_json.py` | JSON ingestion | New file, dedup, extra/missing columns, empty folder, schema extension, data values |
| `test_ingest_xml.py` | XML ingestion | New file, dedup, extra/missing columns, empty folder, malformed file |
| `test_ingest_parquet.py` | Parquet ingestion | New file, dedup, extra/missing columns, empty folder, schema extension, type preservation |

Requirements: `pip install pandas openpyxl pyarrow lxml`

---

## 16. Key Design Decisions

**SCD Type-2 for providers and datasets**  
Editing a provider or dataset never overwrites history. This means if an ingestion run used schema version 3 and the schema was later changed to version 4, the run log still shows which version was active at the time. The business key (`provider_id`, `dataset_id`) is stable across all versions, so foreign keys in `etl_run_log` and the ingestion tables never need to change.

**Business keys, not surrogate keys, in foreign references**  
All relationships between tables use the stable business key (`provider_id`, `dataset_id`), never the surrogate key (`provider_sid`, `dataset_sid`). This means run-log rows and ingestion data rows remain navigable through schema changes — a query like "show me all data loaded for dataset 7" still works even after 20 edits to that dataset's schema.

**Single ingestion table per dataset**  
Each dataset gets its own physical table in the `ingestion` database (`{provider}_{dataset_id}`). This keeps data from different providers and datasets isolated, avoids EAV-style generic tables, and allows normal SQL queries against the loaded data.

**Schema evolution is additive only**  
When a dataset is edited and new columns are added, the physical ingestion table is altered with `ADD COLUMN`. Existing columns are never dropped. This means all previously loaded rows remain consistent — old rows just have NULL in the new columns.

**Deduplication by name + size**  
Files are identified by basename and byte count rather than by path or modification time. A file delivered again with the same name and identical content is skipped. A file with the same name but different size (e.g., an amended delivery) is treated as a new file and loaded.

**Abstract logger with swappable destination**  
The logging system uses an `AppLogger` abstract base class. The two singletons (`ui_logger`, `ingestion_logger`) at the bottom of `logger.py` are the only code to change if logs need to go to a database, a cloud service, or a SIEM platform. No route or engine code needs to change.

**Column transformations applied before schema comparison**  
Transformations (trim, case, null replacement, date reformatting) run on the raw DataFrame before column names are compared against the schema. This means the engine always operates on clean, normalised data, and column-type rules are enforced on the cleansed values.
