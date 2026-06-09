"""REST API routes for dataset management (``/api/datasets``).

Datasets are stored in ``tp_dataset`` using the same SCD Type-2 dual-key
pattern as providers:

- ``dataset_sid``  — surrogate primary key (auto-increment), changes per version.
- ``dataset_id``   — stable business key, set once on creation and preserved
  through all subsequent version INSERTs.
- ``current_flag`` — ``'y'`` live, ``'n'`` superseded, ``'d'`` deleted.

Column definitions live in ``tp_dataset_col`` and are referenced by the
dataset's ``dataset_id`` business key.  On dataset creation a corresponding
physical table is created in the ``ingestion`` database to hold actual data
rows.  On dataset edit, only genuinely new columns are added via ``ALTER TABLE``
(never dropped — backwards-compatible schema evolution).
"""

import os
import re
import uuid
import pandas as pd
from flask import Blueprint, request, jsonify
from db import get_conn, get_ingestion_conn
from logger import ui_logger

datasets_bp = Blueprint('datasets', __name__)

# Temporary upload directory used when inferring column types from a sample file.
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'uploads')

# Maps logical column types (as stored in tp_dataset_col) to MySQL DDL types.
_SQL_TYPE_MAP = {
    'integer':  'BIGINT',
    'decimal':  'DOUBLE',
    'boolean':  'TINYINT(1)',
    'datetime': 'DATETIME',
    'date':     'DATE',
}


def _col_sql_type(col_type):
    """Translate a logical column type to a MySQL DDL type string.

    Args:
        col_type (str | None): Logical type (e.g. ``'integer'``, ``'decimal'``).

    Returns:
        str: MySQL type keyword; defaults to ``'TEXT'`` for unknown types.
    """
    return _SQL_TYPE_MAP.get((col_type or '').lower(), 'TEXT')


def _sanitize_name(name):
    """Convert a free-text name to a SQL-safe identifier.

    Lowercases the input, replaces runs of non-alphanumeric characters with
    underscores, and strips leading/trailing underscores.

    Args:
        name (str): Raw provider or dataset name.

    Returns:
        str: Safe identifier suitable for use in a table or column name.
    """
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9]+', '_', name)
    return name.strip('_')


def _create_ingestion_table(table_name, columns):
    """Create the physical ingestion table in the ``ingestion`` database.

    Builds a ``CREATE TABLE IF NOT EXISTS`` statement that includes one column
    per dataset column definition followed by five mandatory metadata columns
    (``load_id``, ``provider_id``, ``dataset_id``, ``load_date``,
    ``load_process``).  The statement is idempotent — re-running on an
    existing table is a no-op.

    Args:
        table_name (str): Target table name in the ``ingestion`` database.
        columns (list[dict]): Dataset column dicts with keys ``col_name``,
            ``col_type``, and ``col_position``.
    """
    # Build data column definitions sorted by declared position.
    col_defs = [
        f"  `{col['col_name']}` {_col_sql_type(col.get('col_type', 'string'))}"
        for col in sorted(columns, key=lambda c: c.get('col_position', 0))
    ]
    # Append mandatory metadata columns appended to every ingestion table.
    col_defs += [
        '  `load_id`      BIGINT',
        '  `provider_id`  INT',
        '  `dataset_id`   INT',
        '  `load_date`    DATETIME',
        '  `load_process` VARCHAR(255)',
    ]
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{table_name}` (\n"
        + ",\n".join(col_defs)
        + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    ing = get_ingestion_conn()
    try:
        with ing.cursor() as ic:
            ic.execute(ddl)
            ing.commit()
    finally:
        ing.close()


def _alter_ingestion_table(table_name, new_cols):
    """Add new columns to an existing ingestion table (incremental schema evolution).

    Only columns that did not exist in the previous version of the dataset are
    passed here — existing columns are never modified or dropped.

    Args:
        table_name (str): Target table name in the ``ingestion`` database.
        new_cols (list[dict]): New column dicts with ``col_name`` and ``col_type``.
    """
    ing = get_ingestion_conn()
    try:
        with ing.cursor() as ic:
            for col in new_cols:
                ic.execute(
                    f"ALTER TABLE `{table_name}` "
                    f"ADD COLUMN `{col['col_name']}` {_col_sql_type(col.get('col_type', 'string'))}"
                )
            ing.commit()
    finally:
        ing.close()


def _row(c, did):
    """Fetch the current active dataset row by business key.

    Args:
        c: Open pymysql DictCursor.
        did (int): ``dataset_id`` (business key).

    Returns:
        dict | None: The live dataset row, or ``None`` if not found.
    """
    c.execute("SELECT * FROM tp_dataset WHERE dataset_id=%s AND current_flag='y'", (did,))
    return c.fetchone()


def _cols(c, did):
    """Fetch all current active columns for a dataset, ordered by position.

    Args:
        c: Open pymysql DictCursor.
        did (int): ``dataset_id`` (business key).

    Returns:
        list[dict]: Column rows sorted by ``col_position``.
    """
    c.execute(
        "SELECT * FROM tp_dataset_col WHERE dataset_id=%s AND current_flag='y' ORDER BY col_position",
        (did,),
    )
    return c.fetchall()


def _check_landing_folder_exists(landing_folder):
    """Verify that the landing folder path exists on the filesystem.

    Args:
        landing_folder (str | None): Path supplied by the caller.

    Returns:
        str | None: Error message if the folder is missing, ``None`` if it
            exists (or if no path was provided).
    """
    if not landing_folder:
        return None
    if not os.path.isdir(landing_folder):
        return f'Landing folder "{landing_folder}" does not exist on the server'
    return None


def _check_landing_folder(c, landing_folder, provider_id, exclude_dataset_id=None):
    """Ensure a landing folder is not already assigned to a different provider.

    A landing folder must belong to exactly one provider — sharing it across
    providers would cause ingestion runs to pick up each other's files.

    Args:
        c: Open pymysql DictCursor.
        landing_folder (str | None): The folder path to validate.
        provider_id (int): The provider that is claiming the folder.
        exclude_dataset_id (int | None): Dataset business key to exclude from
            the check (used during edits so the current dataset is not flagged
            against itself).

    Returns:
        str | None: Human-readable error message if a conflict is found,
            ``None`` if the folder is available.
    """
    if not landing_folder:
        return None

    sql = (
        "SELECT d.dataset_id, p.provider_name FROM tp_dataset d "
        "JOIN tp_provider p ON p.provider_id=d.provider_id AND p.current_flag='y' "
        "WHERE d.landing_folder=%s AND d.provider_id<>%s AND d.current_flag='y'"
    )
    params = [landing_folder, provider_id]
    if exclude_dataset_id is not None:
        sql += " AND d.dataset_id<>%s"
        params.append(exclude_dataset_id)
    sql += " LIMIT 1"
    c.execute(sql, params)
    conflict = c.fetchone()
    if conflict:
        return (
            f'Landing folder "{landing_folder}" is already used by '
            f'provider "{conflict["provider_name"]}"'
        )
    return None


def _check_file_pattern(c, file_name_pattern, provider_id, exclude_dataset_id=None):
    """Ensure a file name pattern is not already used by another dataset in the same provider.

    Within a provider, each dataset must claim a distinct file pattern so the
    ingestion engine can unambiguously route files to the correct dataset.

    Args:
        c: Open pymysql DictCursor.
        file_name_pattern (str | None): The glob pattern to validate.
        provider_id (int): The provider that owns the dataset.
        exclude_dataset_id (int | None): Dataset business key to exclude from
            the check (used during edits so the current dataset is not flagged
            against itself).

    Returns:
        str | None: Human-readable error message if a conflict is found,
            ``None`` if the pattern is available.
    """
    if not file_name_pattern:
        return None

    sql = (
        "SELECT dataset_id, dataset_name FROM tp_dataset "
        "WHERE provider_id=%s AND file_name_pattern=%s AND current_flag='y'"
    )
    params = [provider_id, file_name_pattern]
    if exclude_dataset_id is not None:
        sql += " AND dataset_id<>%s"
        params.append(exclude_dataset_id)
    sql += " LIMIT 1"
    c.execute(sql, params)
    conflict = c.fetchone()
    if conflict:
        return (
            f'File pattern "{file_name_pattern}" is already used by '
            f'dataset "{conflict["dataset_name"]}" in this provider'
        )
    return None


def _infer_type(series):
    """Infer the logical column type from a pandas Series dtype.

    Args:
        series (pd.Series): Column data from a sample file.

    Returns:
        str: One of ``'integer'``, ``'decimal'``, ``'boolean'``, ``'datetime'``,
            or ``'string'``.
    """
    if pd.api.types.is_integer_dtype(series):
        return 'integer'
    if pd.api.types.is_float_dtype(series):
        return 'decimal'
    if pd.api.types.is_bool_dtype(series):
        return 'boolean'
    if pd.api.types.is_datetime64_any_dtype(series):
        return 'datetime'
    return 'string'


def _read_sample(filepath, fmt):
    """Read up to 100 rows from a file to infer column types.

    Args:
        filepath (str): Absolute path to the sample file.
        fmt (str): File format key (``'csv'``, ``'xlsx'``, ``'json'``,
            ``'xml'``, ``'parquet'``).

    Returns:
        pd.DataFrame: Sample data frame.

    Raises:
        ValueError: If the format is not supported.
    """
    if fmt == 'csv':
        return pd.read_csv(filepath, nrows=100)
    elif fmt == 'xlsx':
        return pd.read_excel(filepath, nrows=100)
    elif fmt == 'json':
        return pd.read_json(filepath).head(100)
    elif fmt == 'xml':
        return pd.read_xml(filepath).head(100)
    elif fmt == 'parquet':
        return pd.read_parquet(filepath).head(100)
    else:
        raise ValueError(f'Unsupported format: {fmt}')


@datasets_bp.get('/datasets')
def list_datasets():
    """Return all active datasets with their column definitions.

    Query params:
        q (str): Optional substring search on ``dataset_name`` or ``dataset_desc``.
        provider_id (int): Filter to datasets belonging to a specific provider.

    Returns:
        flask.Response: JSON array of dataset rows; each row includes a
            ``columns`` key with the associated column list.
    """
    search = request.args.get('q', '').strip()
    provider_id = request.args.get('provider_id')
    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Base query joins the current provider version for provider_name.
            sql = (
                "SELECT d.*, p.provider_name FROM tp_dataset d "
                "JOIN tp_provider p ON p.provider_id=d.provider_id AND p.current_flag='y' "
                "WHERE d.current_flag='y'"
            )
            params = []
            if provider_id:
                sql += " AND d.provider_id=%s"
                params.append(provider_id)
            if search:
                sql += " AND (d.dataset_name LIKE %s OR d.dataset_desc LIKE %s)"
                params += [f'%{search}%', f'%{search}%']
            sql += " ORDER BY p.provider_name, d.dataset_name"
            c.execute(sql, params)
            rows = c.fetchall()
            # Attach column definitions to each dataset row.
            for row in rows:
                c.execute(
                    "SELECT col_name, col_type, col_position FROM tp_dataset_col "
                    "WHERE dataset_id=%s AND current_flag='y' ORDER BY col_position",
                    (row['dataset_id'],),
                )
                row['columns'] = c.fetchall()
            return jsonify(rows)
    finally:
        conn.close()


@datasets_bp.get('/datasets/<int:did>')
def get_dataset(did):
    """Return a single active dataset with its columns by business key.

    Args:
        did (int): ``dataset_id`` path parameter.

    Returns:
        flask.Response: JSON dataset row with ``columns`` list, or 404.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            row = _row(c, did)
            if not row:
                return jsonify({'error': 'Not found'}), 404
            row['columns'] = _cols(c, did)
            return jsonify(row)
    finally:
        conn.close()


@datasets_bp.post('/datasets/upload')
def upload_file():
    """Upload a sample file and return inferred column definitions plus sample rows.

    The file is saved to a temporary path, read by the appropriate pandas
    reader (up to 100 rows), and then deleted.  Column types are inferred from
    pandas dtypes.

    Returns:
        flask.Response: JSON with ``columns``, ``sample_rows``, ``tmp_file``,
            and ``row_count``.  Returns 400 on missing file or read errors.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    fmt = request.form.get('data_format', '').lower()
    ext = os.path.splitext(f.filename)[1].lstrip('.').lower()
    file_fmt = 'xlsx' if ext == 'xls' else ext
    if not fmt:
        # Fall back to file extension when the caller doesn't specify the format.
        fmt = file_fmt
    else:
        # Reject the upload early if the file extension does not match the declared format.
        if file_fmt != fmt:
            ui_logger.warning('Sample upload rejected — format mismatch',
                              filename=f.filename, declared_format=fmt, file_ext=ext)
            return jsonify({
                'error': f'File ".{ext}" does not match selected format "{fmt.upper()}"'
            }), 400

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    # Use a UUID to avoid name collisions from concurrent uploads.
    tmp_name = f'{uuid.uuid4().hex}.{fmt}'
    tmp_path = os.path.join(UPLOAD_FOLDER, tmp_name)
    f.save(tmp_path)

    try:
        df = _read_sample(tmp_path, fmt)
        columns = [
            {'col_name': col, 'col_type': _infer_type(df[col]), 'col_position': i + 1}
            for i, col in enumerate(df.columns)
        ]
        # Convert to strings so JSON serialization never fails on mixed types.
        sample_rows = df.head(10).fillna('').astype(str).to_dict(orient='records')
        ui_logger.info('Sample file uploaded', filename=f.filename, fmt=fmt,
                       columns=len(columns), rows=len(df))
        return jsonify({
            'columns': columns,
            'sample_rows': sample_rows,
            'tmp_file': tmp_name,
            'row_count': len(df),
        })
    except Exception as e:
        ui_logger.error('Sample file read failed', filename=f.filename, fmt=fmt, error=str(e))
        return jsonify({'error': str(e)}), 400
    finally:
        # Always clean up the temporary file regardless of success or failure.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@datasets_bp.post('/datasets')
def add_dataset():
    """Create a new dataset using the two-step business-key pattern.

    Like providers, the ``dataset_id`` business key must equal the first
    ``dataset_sid`` (AUTO_INCREMENT).  The ``target_table`` name is also
    derived from ``dataset_id``, so both are set in the same UPDATE after the
    initial INSERT.

    Naming scheme for the ingestion table:
        ``{sanitized_provider_name}_{dataset_sid}``

    After the metadata is committed, the physical ingestion table is created
    via ``_create_ingestion_table`` (DDL is idempotent).

    Returns:
        flask.Response: JSON of the created dataset with HTTP 201, or 400/404/409.
    """
    data = request.json or {}
    name = (data.get('dataset_name') or '').strip()
    provider_id = data.get('provider_id')
    if not name or not provider_id:
        return jsonify({'error': 'dataset_name and provider_id are required'}), 400

    columns = data.get('columns', [])

    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Validate that the referenced provider exists and is active.
            c.execute(
                "SELECT provider_id, provider_name FROM tp_provider "
                "WHERE provider_id=%s AND current_flag='y'",
                (provider_id,),
            )
            prov = c.fetchone()
            if not prov:
                ui_logger.warning('Dataset creation rejected — provider not found',
                                  provider_id=provider_id, name=name)
                return jsonify({'error': 'Provider not found'}), 404

            # Verify the landing folder exists on the server filesystem.
            exists_err = _check_landing_folder_exists(data.get('landing_folder'))
            if exists_err:
                ui_logger.warning('Dataset creation rejected — landing folder missing',
                                  provider_id=provider_id, name=name,
                                  folder=data.get('landing_folder'))
                return jsonify({'error': exists_err}), 400

            # Reject duplicate dataset names within the same provider.
            c.execute(
                "SELECT dataset_id FROM tp_dataset "
                "WHERE LOWER(dataset_name)=LOWER(%s) AND provider_id=%s AND current_flag='y'",
                (name, provider_id),
            )
            if c.fetchone():
                ui_logger.warning('Dataset creation rejected — duplicate name',
                                  provider_id=provider_id, name=name)
                return jsonify({'error': f'Dataset "{name}" already exists for this provider'}), 409

            # Reject if the landing folder is already owned by a different provider.
            folder_err = _check_landing_folder(c, data.get('landing_folder'), provider_id)
            if folder_err:
                ui_logger.warning('Dataset creation rejected — landing folder conflict',
                                  provider_id=provider_id, name=name,
                                  folder=data.get('landing_folder'))
                return jsonify({'error': folder_err}), 409

            # Reject if another dataset in this provider already uses the same file pattern.
            pattern_err = _check_file_pattern(c, data.get('file_name_pattern'), provider_id)
            if pattern_err:
                ui_logger.warning('Dataset creation rejected — file pattern conflict',
                                  provider_id=provider_id, name=name,
                                  pattern=data.get('file_name_pattern'))
                return jsonify({'error': pattern_err}), 409

            # Step 1: insert without business key or target_table (both need dataset_id first).
            c.execute(
                "INSERT INTO tp_dataset "
                "(provider_id, dataset_name, dataset_desc, frequency, data_format, "
                "file_name_pattern, landing_folder, version, current_flag, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,1,'y',%s)",
                (
                    provider_id, name,
                    data.get('dataset_desc'), data.get('frequency'),
                    data.get('data_format'), data.get('file_name_pattern'),
                    data.get('landing_folder'), data.get('created_by', 'system'),
                ),
            )
            new_sid = c.lastrowid

            # Build target table name: sanitized_provider_name + dataset_id.
            sanitized = _sanitize_name(prov['provider_name']) or 'provider'
            target_table = f"{sanitized}_{new_sid}"

            # Step 2: set business key and target_table together in one UPDATE.
            c.execute(
                "UPDATE tp_dataset SET dataset_id=%s, target_table=%s WHERE dataset_sid=%s",
                (new_sid, target_table, new_sid),
            )

            # Insert column definitions referencing the new business key.
            for col in columns:
                c.execute(
                    "INSERT INTO tp_dataset_col "
                    "(dataset_id, col_name, col_type, col_position, version, current_flag) "
                    "VALUES (%s,%s,%s,%s,1,'y')",
                    (new_sid, col['col_name'], col.get('col_type', 'string'),
                     col.get('col_position', 0)),
                )

            # Create the physical ingestion table (DDL runs outside metadata transaction).
            _create_ingestion_table(target_table, columns)

            conn.commit()
            ui_logger.info('Dataset created', dataset_id=new_sid, name=name,
                           provider_id=provider_id, target_table=target_table,
                           columns=len(columns))
            row = _row(c, new_sid)
            row['columns'] = _cols(c, new_sid)
            return jsonify(row), 201
    except Exception as e:
        ui_logger.error('Dataset creation failed', name=name, provider_id=provider_id,
                        error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@datasets_bp.put('/datasets/<int:did>')
def edit_dataset(did):
    """Update a dataset by creating a new SCD-2 version.

    Column handling during an edit:

    - Existing columns are preserved unchanged (positions and types carried over).
    - New columns submitted in the request are appended after the last existing
      position.
    - Removed columns are *not* dropped — they stay in the merged list so that
      data already loaded under the old schema remains consistent.
    - Any genuinely new columns are also added to the physical ingestion table
      via ``ALTER TABLE ADD COLUMN``.

    Args:
        did (int): ``dataset_id`` (business key) — stable across all versions.

    Returns:
        flask.Response: JSON of the updated dataset row with columns, or 400/404/409.
    """
    data = request.json or {}
    name = (data.get('dataset_name') or '').strip()
    provider_id = data.get('provider_id')
    if not name or not provider_id:
        return jsonify({'error': 'dataset_name and provider_id are required'}), 400

    new_columns = data.get('columns', [])

    conn = get_conn()
    try:
        with conn.cursor() as c:
            old = _row(c, did)
            if not old:
                ui_logger.warning('Dataset update rejected — not found', dataset_id=did)
                return jsonify({'error': 'Not found'}), 404

            # Verify the landing folder exists on the server filesystem.
            exists_err = _check_landing_folder_exists(data.get('landing_folder'))
            if exists_err:
                ui_logger.warning('Dataset update rejected — landing folder missing',
                                  dataset_id=did, folder=data.get('landing_folder'))
                return jsonify({'error': exists_err}), 400

            # Reject name collision with a *different* active dataset in the same provider.
            c.execute(
                "SELECT dataset_id FROM tp_dataset "
                "WHERE LOWER(dataset_name)=LOWER(%s) AND provider_id=%s "
                "AND current_flag='y' AND dataset_id<>%s",
                (name, provider_id, did),
            )
            if c.fetchone():
                ui_logger.warning('Dataset update rejected — duplicate name',
                                  dataset_id=did, name=name, provider_id=provider_id)
                return jsonify({'error': f'Dataset "{name}" already exists for this provider'}), 409

            # Reject if the landing folder is already owned by a different provider (exclude self).
            folder_err = _check_landing_folder(c, data.get('landing_folder'), provider_id, did)
            if folder_err:
                ui_logger.warning('Dataset update rejected — landing folder conflict',
                                  dataset_id=did, folder=data.get('landing_folder'))
                return jsonify({'error': folder_err}), 409

            # Reject if another dataset in this provider already uses the same file pattern (exclude self).
            pattern_err = _check_file_pattern(c, data.get('file_name_pattern'), provider_id, did)
            if pattern_err:
                ui_logger.warning('Dataset update rejected — file pattern conflict',
                                  dataset_id=did, pattern=data.get('file_name_pattern'))
                return jsonify({'error': pattern_err}), 409

            # Carry the target_table name forward unchanged (table name never changes).
            target_table = old.get('target_table')

            # Merge columns: keep existing, identify genuinely new ones for ALTER TABLE.
            existing_cols = _cols(c, did)
            existing_names = {col['col_name'].lower(): col for col in existing_cols}

            merged = list(existing_cols)
            added_cols = []
            max_pos = max((col['col_position'] for col in existing_cols), default=0)
            for nc in new_columns:
                if nc['col_name'].lower() not in existing_names:
                    # Assign the next available position for the new column.
                    max_pos += 1
                    new_col = {
                        'col_name': nc['col_name'],
                        'col_type': nc.get('col_type', 'string'),
                        'col_position': max_pos,
                    }
                    merged.append(new_col)
                    added_cols.append(new_col)
                else:
                    # Update the type on existing columns (position stays the same).
                    existing_names[nc['col_name'].lower()]['col_type'] = nc.get('col_type', 'string')

            new_version = old['version'] + 1

            # Supersede the current version row via its surrogate key.
            c.execute(
                "UPDATE tp_dataset SET current_flag='n', updated_by=%s WHERE dataset_sid=%s",
                (data.get('updated_by', 'system'), old['dataset_sid']),
            )
            # Supersede all current column rows via the business key.
            c.execute(
                "UPDATE tp_dataset_col SET current_flag='n' "
                "WHERE dataset_id=%s AND current_flag='y'",
                (did,),
            )

            # Insert the new dataset version, carrying the business key and target_table.
            c.execute(
                "INSERT INTO tp_dataset "
                "(dataset_id, provider_id, dataset_name, dataset_desc, frequency, data_format, "
                "file_name_pattern, landing_folder, target_table, version, current_flag, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'y',%s)",
                (
                    did, provider_id, name,
                    data.get('dataset_desc'), data.get('frequency'),
                    data.get('data_format'), data.get('file_name_pattern'),
                    data.get('landing_folder'), target_table,
                    new_version, data.get('updated_by', 'system'),
                ),
            )

            # Re-insert the full merged column list under the new version number.
            for col in merged:
                c.execute(
                    "INSERT INTO tp_dataset_col "
                    "(dataset_id, col_name, col_type, col_position, version, current_flag) "
                    "VALUES (%s,%s,%s,%s,%s,'y')",
                    (did, col['col_name'], col.get('col_type', 'string'),
                     col.get('col_position', 0), new_version),
                )

            # ALTER the physical table to add only the incremental (new) columns.
            if added_cols and target_table:
                _alter_ingestion_table(target_table, added_cols)

            conn.commit()
            ui_logger.info('Dataset updated', dataset_id=did, name=name,
                           provider_id=provider_id, version=new_version,
                           added_columns=len(added_cols))
            row = _row(c, did)
            row['columns'] = _cols(c, did)
            return jsonify(row)
    except Exception as e:
        ui_logger.error('Dataset update failed', dataset_id=did, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@datasets_bp.get('/datasets/<int:did>/transforms')
def get_transforms(did):
    """Return transformation rules merged with the dataset's current column list.

    Columns that have no saved transforms are still returned so the UI can
    render a full table — their transform fields will all be ``None``.

    Args:
        did (int): ``dataset_id`` (business key).

    Returns:
        flask.Response: JSON ``{ dataset: {...}, transforms: [ {col_name, col_type,
            trim_option, null_replace, case_option, date_fmt_from, date_fmt_to}, … ] }``,
            or 404 if the dataset does not exist.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Verify the dataset exists and join provider name for the page header.
            c.execute(
                "SELECT d.*, p.provider_name FROM tp_dataset d "
                "JOIN tp_provider p ON p.provider_id=d.provider_id AND p.current_flag='y' "
                "WHERE d.dataset_id=%s AND d.current_flag='y'",
                (did,),
            )
            dataset = c.fetchone()
            if not dataset:
                return jsonify({'error': 'Not found'}), 404

            # Load the current active columns ordered by position.
            c.execute(
                "SELECT col_name, col_type, col_position FROM tp_dataset_col "
                "WHERE dataset_id=%s AND current_flag='y' ORDER BY col_position",
                (did,),
            )
            cols = c.fetchall()

            # Load any existing transform rows and index them by column name.
            c.execute(
                "SELECT col_name, trim_option, null_replace, case_option, "
                "date_fmt_from, date_fmt_to FROM tp_dataset_col_transforms "
                "WHERE dataset_id=%s",
                (did,),
            )
            saved = {r['col_name'].lower(): r for r in c.fetchall()}

            # Merge: every column gets a transform entry (empty fields when not saved).
            transforms = []
            for col in cols:
                t = saved.get(col['col_name'].lower(), {})
                transforms.append({
                    'col_name':     col['col_name'],
                    'col_type':     col['col_type'] or 'string',
                    'trim_option':  t.get('trim_option'),
                    'null_replace': t.get('null_replace'),
                    'case_option':  t.get('case_option'),
                    'date_fmt_from': t.get('date_fmt_from'),
                    'date_fmt_to':   t.get('date_fmt_to'),
                })

            return jsonify({'dataset': dataset, 'transforms': transforms})
    finally:
        conn.close()


@datasets_bp.put('/datasets/<int:did>/transforms')
def save_transforms(did):
    """Replace all transformation rules for a dataset.

    Rows where every transform field is empty are not persisted — they are
    treated as "no transformation required" and skipped.  An empty
    ``null_replace`` value from the client is normalised to ``None`` (the
    user left the field blank = no null-replacement configured).

    Args:
        did (int): ``dataset_id`` (business key).

    Returns:
        flask.Response: JSON ``{ message, saved }`` count, or 404.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            if not _row(c, did):
                ui_logger.warning('Transforms save rejected — dataset not found', dataset_id=did)
                return jsonify({'error': 'Not found'}), 404

            incoming = request.json or []

            # Delete the current transform set then re-insert only rows that carry work.
            c.execute("DELETE FROM tp_dataset_col_transforms WHERE dataset_id=%s", (did,))

            saved_count = 0
            for t in incoming:
                trim      = t.get('trim_option')  or None
                null_rep  = t.get('null_replace')        # empty string == not set
                if null_rep == '':
                    null_rep = None
                case      = t.get('case_option')  or None
                fmt_from  = t.get('date_fmt_from') or None
                fmt_to    = t.get('date_fmt_to')   or None

                # Skip rows where no transformation is configured.
                if not any([trim, null_rep is not None, case, fmt_from, fmt_to]):
                    continue

                c.execute(
                    "INSERT INTO tp_dataset_col_transforms "
                    "(dataset_id, col_name, trim_option, null_replace, "
                    "case_option, date_fmt_from, date_fmt_to) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (did, t['col_name'], trim, null_rep, case, fmt_from, fmt_to),
                )
                saved_count += 1

            conn.commit()
            ui_logger.info('Transforms saved', dataset_id=did, saved=saved_count)
            return jsonify({'message': 'Saved', 'saved': saved_count})
    except Exception as e:
        ui_logger.error('Transforms save failed', dataset_id=did, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@datasets_bp.delete('/datasets/<int:did>')
def delete_dataset(did):
    """Logically delete a dataset and its column definitions.

    Sets ``current_flag = 'd'`` on all version rows for the dataset and its
    columns.  The physical ingestion table in the ``ingestion`` database is
    *not* dropped — historical data is preserved.

    Args:
        did (int): ``dataset_id`` (business key).

    Returns:
        flask.Response: JSON confirmation, or 404 if not found.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            old = _row(c, did)
            if not old:
                ui_logger.warning('Dataset delete rejected — not found', dataset_id=did)
                return jsonify({'error': 'Not found'}), 404

            c.execute(
                "UPDATE tp_dataset SET current_flag='d' WHERE dataset_id=%s", (did,)
            )
            c.execute(
                "UPDATE tp_dataset_col SET current_flag='d' WHERE dataset_id=%s", (did,)
            )
            conn.commit()
            ui_logger.info('Dataset deleted', dataset_id=did, name=old['dataset_name'],
                           provider_id=old['provider_id'])
            return jsonify({'message': 'Deleted', 'dataset_id': did})
    except Exception as e:
        ui_logger.error('Dataset delete failed', dataset_id=did, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()
