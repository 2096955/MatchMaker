"""REST API routes for ingestion triggering and run-log reporting.

Exposes three endpoints:

- ``POST /api/ingest/<dataset_id>``  — Trigger a synchronous ingestion run for
  the specified dataset.  The engine creates an ``in_progress`` log stub
  immediately so polling clients can detect activity while the POST is
  in-flight.
- ``GET  /api/reports``              — Query ``etl_run_log`` with optional
  filters (provider, dataset, status, date range).  Returns up to 500 rows.
- ``GET  /api/reports/<run_id>``     — Fetch a single run-log entry by ID,
  joined to provider and dataset names.
"""

from flask import Blueprint, request, jsonify
from db import get_conn
from ingestion.factory import get_ingester
from logger import ui_logger

ingest_bp = Blueprint('ingest', __name__)


@ingest_bp.post('/ingest/<int:dataset_id>')
def trigger_ingest(dataset_id):
    """Trigger a synchronous ingestion run for a dataset.

    Delegates to ``get_ingester`` to resolve the correct format-specific engine
    subclass, then calls ``engine.run()`` which:

    1. Discovers files in the landing folder matching the configured pattern.
    2. Skips files already processed (deduplication by name + size).
    3. Reads, compares columns, bulk-loads to the ingestion table, and moves
       processed files.
    4. Returns a list of ``etl_run_log`` dicts (one per file, or one skipped
       entry if no files were found).

    Args:
        dataset_id (int): ``dataset_id`` path parameter (business key).

    Returns:
        flask.Response: JSON array of run-log dicts with HTTP 200, or 404 if
            the dataset is not found or has an unsupported format.
    """
    ui_logger.info('Ingestion triggered via API', dataset_id=dataset_id)
    try:
        ingester = get_ingester(dataset_id)
        results = ingester.run()
        statuses = [r.get('run_status') for r in results if r]
        ui_logger.info('Ingestion API call completed',
                       dataset_id=dataset_id, files=len(results), statuses=statuses)
        return jsonify(results), 200
    except ValueError as e:
        # get_ingester raises ValueError for missing dataset or unknown format.
        ui_logger.warning('Ingestion trigger rejected — dataset not found or bad format',
                          dataset_id=dataset_id, error=str(e))
        return jsonify({'error': str(e)}), 404


@ingest_bp.get('/reports')
def list_reports():
    """Return ETL run-log entries matching the supplied filters.

    All filters are optional — omitting all of them returns the 500 most
    recent runs.  Multiple status values can be supplied as a comma-separated
    string (e.g. ``status=success,failed``).

    Query params:
        provider_id (int):  Filter to runs for a specific provider.
        dataset_id  (int):  Filter to runs for a specific dataset.
        status      (str):  Comma-separated list of ``run_status`` values.
        from_date   (str):  ISO date ``YYYY-MM-DD``; include runs on or after.
        to_date     (str):  ISO date ``YYYY-MM-DD``; include runs up to end of day.

    Returns:
        flask.Response: JSON array of run-log rows enriched with
            ``provider_name`` and ``dataset_name``, ordered by ``run_date DESC``.
    """
    p = request.args
    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Base query joins current provider and dataset versions for display names.
            sql = (
                "SELECT r.*, p.provider_name, d.dataset_name "
                "FROM etl_run_log r "
                "LEFT JOIN tp_provider p ON p.provider_id=r.provider_id AND p.current_flag='y' "
                "LEFT JOIN tp_dataset  d ON d.dataset_id=r.dataset_id  AND d.current_flag='y' "
                "WHERE 1=1"
            )
            params = []
            if p.get('provider_id'):
                sql += ' AND r.provider_id=%s'
                params.append(p['provider_id'])
            if p.get('dataset_id'):
                sql += ' AND r.dataset_id=%s'
                params.append(p['dataset_id'])
            if p.get('status'):
                # Support comma-separated list of status values.
                statuses = [s.strip() for s in p['status'].split(',') if s.strip()]
                if statuses:
                    fmt_ph = ','.join(['%s'] * len(statuses))
                    sql += f' AND r.run_status IN ({fmt_ph})'
                    params.extend(statuses)
            if p.get('from_date'):
                sql += ' AND r.run_date>=%s'
                params.append(p['from_date'])
            if p.get('to_date'):
                # Append end-of-day time so the to_date is inclusive.
                sql += ' AND r.run_date<=%s'
                params.append(p['to_date'] + ' 23:59:59')
            sql += ' ORDER BY r.run_date DESC LIMIT 500'
            c.execute(sql, params)
            return jsonify(c.fetchall())
    finally:
        conn.close()


@ingest_bp.get('/reports/<int:run_id>')
def get_report(run_id):
    """Return a single ETL run-log entry by run ID.

    The result is joined to the current provider and dataset versions so the
    response includes human-readable ``provider_name`` and ``dataset_name``.

    Args:
        run_id (int): ``run_id`` path parameter.

    Returns:
        flask.Response: JSON run-log row, or 404 if not found.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT r.*, p.provider_name, d.dataset_name "
                "FROM etl_run_log r "
                "LEFT JOIN tp_provider p ON p.provider_id=r.provider_id AND p.current_flag='y' "
                "LEFT JOIN tp_dataset  d ON d.dataset_id=r.dataset_id  AND d.current_flag='y' "
                "WHERE r.run_id=%s",
                (run_id,),
            )
            row = c.fetchone()
            if not row:
                return jsonify({'error': 'Not found'}), 404
            return jsonify(row)
    finally:
        conn.close()
