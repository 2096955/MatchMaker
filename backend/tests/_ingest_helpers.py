"""Shared helpers for ingestion engine tests.

Not a test file — imported by test_ingest_*.py modules.
"""
import os
import sys
import shutil
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd
import pymysql
from pymysql.cursors import DictCursor

from app import app
from ingestion.factory import get_ingester

app.config['TESTING'] = True
client = app.test_client()

SAMPLE_COLS = [
    {'col_name': 'id',     'col_type': 'integer', 'col_position': 1},
    {'col_name': 'name',   'col_type': 'string',  'col_position': 2},
    {'col_name': 'amount', 'col_type': 'decimal', 'col_position': 3},
]

SAMPLE_ROWS = [
    {'id': 1, 'name': 'Alice',   'amount': 100.50},
    {'id': 2, 'name': 'Bob',     'amount': 200.75},
    {'id': 3, 'name': 'Charlie', 'amount': 300.00},
    {'id': 4, 'name': 'Diana',   'amount': 450.25},
    {'id': 5, 'name': 'Eve',     'amount': 500.00},
]


def sample_df():
    return pd.DataFrame(SAMPLE_ROWS)


def sample_df_extra_cols():
    """Five rows with two extra columns beyond the registered schema."""
    df = sample_df()
    df['status'] = 'active'
    df['category'] = 'A'
    return df


def sample_df_missing_col():
    """Five rows missing the 'amount' column."""
    return pd.DataFrame([{'id': r['id'], 'name': r['name']} for r in SAMPLE_ROWS])


# ── File writers ──────────────────────────────────────────────────────────────

def write_csv(folder, filename='data.csv', df=None):
    path = os.path.join(folder, filename)
    (df if df is not None else sample_df()).to_csv(path, index=False)
    return path


def write_txt(folder, filename='data.txt', delimiter=',', df=None):
    path = os.path.join(folder, filename)
    (df if df is not None else sample_df()).to_csv(path, index=False, sep=delimiter)
    return path


def write_xlsx(folder, filename='data.xlsx', df=None):
    path = os.path.join(folder, filename)
    (df if df is not None else sample_df()).to_excel(path, index=False)
    return path


def write_json(folder, filename='data.json', df=None):
    path = os.path.join(folder, filename)
    (df if df is not None else sample_df()).to_json(path, orient='records', indent=2)
    return path


def write_xml(folder, filename='data.xml', df=None):
    df = df if df is not None else sample_df()
    path = os.path.join(folder, filename)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<data>']
    for _, row in df.iterrows():
        lines.append('  <row>')
        for col in df.columns:
            lines.append(f'    <{col}>{row[col]}</{col}>')
        lines.append('  </row>')
    lines.append('</data>')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))
    return path


def write_parquet(folder, filename='data.parquet', df=None):
    path = os.path.join(folder, filename)
    (df if df is not None else sample_df()).to_parquet(path, index=False)
    return path


# ── Dataset lifecycle ─────────────────────────────────────────────────────────

def create_dataset(fmt, pattern, extra_payload=None):
    """Create a provider + dataset in a fresh temp landing folder.

    Returns:
        dict with keys: provider_id, dataset_id, target_table, landing_dir
    """
    landing = tempfile.mkdtemp(prefix='ingest_test_')

    r = client.post('/api/providers', json={
        'provider_name': f'__test_prov_{fmt}_{os.getpid()}',
        'provider_desc': 'Test ingestion provider',
    })
    assert r.status_code == 201, f"Provider create failed: {r.data}"
    provider_id = r.get_json()['provider_id']

    payload = {
        'provider_id':       provider_id,
        'dataset_name':      f'__test_ds_{fmt}_{os.getpid()}',
        'data_format':       fmt,
        'file_name_pattern': pattern,
        'landing_folder':    landing,
        'columns':           SAMPLE_COLS,
    }
    if extra_payload:
        payload.update(extra_payload)

    r = client.post('/api/datasets', json=payload)
    assert r.status_code == 201, f"Dataset create failed: {r.data}"
    ds = r.get_json()

    return {
        'provider_id':  provider_id,
        'dataset_id':   ds['dataset_id'],
        'target_table': ds['target_table'],
        'landing_dir':  landing,
    }


def destroy_dataset(ctx):
    """Hard-delete all rows and drop the ingestion table for a test dataset."""
    conn = pymysql.connect(
        host='localhost',
        user=os.environ.get('my_sql_user', 'root'),
        password=os.environ.get('my_sql_password', ''),
        database='metadata', charset='utf8mb4',
        cursorclass=DictCursor, autocommit=False,
    )
    ing = pymysql.connect(
        host='localhost',
        user=os.environ.get('my_sql_user', 'root'),
        password=os.environ.get('my_sql_password', ''),
        database='ingestion', charset='utf8mb4',
        cursorclass=DictCursor, autocommit=False,
    )
    try:
        did = ctx['dataset_id']
        pid = ctx['provider_id']
        with conn.cursor() as c:
            c.execute("DELETE FROM etl_run_log               WHERE dataset_id = %s", (did,))
            c.execute("DELETE FROM tp_dataset_col_transforms  WHERE dataset_id = %s", (did,))
            c.execute("DELETE FROM tp_dataset_col             WHERE dataset_id = %s", (did,))
            c.execute("DELETE FROM tp_dataset                 WHERE dataset_id = %s", (did,))
            c.execute("DELETE FROM tp_provider                WHERE provider_id = %s", (pid,))
        conn.commit()
        if ctx.get('target_table'):
            with ing.cursor() as ic:
                ic.execute(f"DROP TABLE IF EXISTS `{ctx['target_table']}`")
            ing.commit()
    finally:
        conn.close()
        ing.close()
    shutil.rmtree(ctx['landing_dir'], ignore_errors=True)


def run_ingestion(dataset_id):
    """Trigger the ingestion engine and return the list of run-log dicts."""
    return get_ingester(dataset_id).run()


def extend_schema(dataset_id, extra_cols):
    """Add extra_cols to the current dataset schema via PUT /api/datasets/<id>."""
    _, ds = client.get(f'/api/datasets/{dataset_id}'), None
    ds_resp = client.get(f'/api/datasets/{dataset_id}')
    assert ds_resp.status_code == 200
    ds = ds_resp.get_json()
    new_cols = ds['columns'] + extra_cols
    r = client.put(f'/api/datasets/{dataset_id}', json={
        'provider_id':       ds['provider_id'],
        'dataset_name':      ds['dataset_name'],
        'data_format':       ds['data_format'],
        'file_name_pattern': ds['file_name_pattern'],
        'landing_folder':    ds['landing_folder'],
        'columns':           new_cols,
    })
    assert r.status_code == 200, f"Schema extend failed: {r.data}"


def query_ingestion_table(target_table, column=None):
    """Return all rows from the ingestion table, optionally selecting one column."""
    ing = pymysql.connect(
        host='localhost',
        user=os.environ.get('my_sql_user', 'root'),
        password=os.environ.get('my_sql_password', ''),
        database='ingestion', charset='utf8mb4',
        cursorclass=DictCursor, autocommit=False,
    )
    try:
        col_expr = f'`{column}`' if column else '*'
        with ing.cursor() as c:
            c.execute(f"SELECT {col_expr} FROM `{target_table}` ORDER BY id")
            return c.fetchall()
    finally:
        ing.close()
