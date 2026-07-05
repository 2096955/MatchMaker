"""REST API routes for provider management (``/api/providers``).

Providers are stored in ``tp_provider`` using an SCD Type-2 versioning pattern:

- ``provider_sid`` — surrogate primary key, changes with every version INSERT.
- ``provider_id``  — stable business key, set once (= first ``provider_sid``)
  and carried unchanged through all future versions.
- ``current_flag`` — ``'y'`` for the live version, ``'n'`` for superseded
  versions, ``'d'`` for logically deleted versions.

All FK references in other tables use ``provider_id`` (business key), never
the surrogate, so history rows remain navigable without dangling references.
"""

from flask import Blueprint, request, jsonify
from db import get_conn
from logger import ui_logger

providers_bp = Blueprint("providers", __name__)


def _row(c, pid):
    """Fetch the current active provider row by business key.

    Args:
        c: Open psycopg cursor (dict rows).
        pid (int): ``provider_id`` (business key).

    Returns:
        dict | None: The live provider row, or ``None`` if not found.
    """
    c.execute(
        "SELECT * FROM tp_provider WHERE provider_id=%s AND current_flag='y'", (pid,)
    )
    return c.fetchone()


@providers_bp.get("/providers")
def list_providers():
    """Return all active providers, optionally filtered by a search term.

    Query params:
        q (str): Optional substring to match against ``provider_name`` or
            ``provider_desc`` (case-insensitive LIKE).

    Returns:
        flask.Response: JSON array of provider rows ordered by ``provider_name``.
    """
    search = request.args.get("q", "").strip()
    conn = get_conn()
    try:
        with conn.cursor() as c:
            if search:
                c.execute(
                    "SELECT * FROM tp_provider WHERE current_flag='y' "
                    "AND (provider_name LIKE %s OR provider_desc LIKE %s) "
                    "ORDER BY provider_name",
                    (f"%{search}%", f"%{search}%"),
                )
            else:
                c.execute(
                    "SELECT * FROM tp_provider WHERE current_flag='y' ORDER BY provider_name"
                )
            return jsonify(c.fetchall())
    finally:
        conn.close()


@providers_bp.get("/providers/<int:pid>")
def get_provider(pid):
    """Return a single active provider by business key.

    Args:
        pid (int): ``provider_id`` path parameter.

    Returns:
        flask.Response: JSON provider row, or 404 if not found.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            row = _row(c, pid)
            if not row:
                return jsonify({"error": "Not found"}), 404
            return jsonify(row)
    finally:
        conn.close()


@providers_bp.post("/providers")
def add_provider():
    """Create a new provider using the two-step business-key pattern.

    The INSERT cannot set ``provider_id`` and ``provider_sid`` to the same
    value in one statement because ``provider_sid`` is a SERIAL identity and its
    value is only known *after* the INSERT.  The workaround:

    1. INSERT without ``provider_id`` (column defaults to NULL).
    2. UPDATE ``provider_id`` to the ``RETURNING provider_sid`` value - this
       makes the business key equal to the first surrogate key and establishes
       the stable identity.

    Returns:
        flask.Response: JSON of the created provider with HTTP 201, or 400/409
            on validation/duplicate errors.
    """
    data = request.json or {}
    name = (data.get("provider_name") or "").strip()
    if not name:
        return jsonify({"error": "provider_name is required"}), 400

    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Reject duplicate active provider names (case-insensitive).
            c.execute(
                "SELECT provider_id FROM tp_provider "
                "WHERE LOWER(provider_name)=LOWER(%s) AND current_flag='y'",
                (name,),
            )
            if c.fetchone():
                ui_logger.warning(
                    "Provider creation rejected — duplicate name", name=name
                )
                return jsonify({"error": f'Provider "{name}" already exists'}), 409

            # Step 1: insert without business key (provider_id left NULL).
            c.execute(
                "INSERT INTO tp_provider (provider_name, provider_desc, provider_website, "
                "version, current_flag, created_by) VALUES (%s,%s,%s,1,'y',%s) "
                "RETURNING provider_sid",
                (
                    name,
                    data.get("provider_desc"),
                    data.get("provider_website"),
                    data.get("created_by", "system"),
                ),
            )
            new_sid = c.fetchone()["provider_sid"]

            # Step 2: set business key = surrogate key, establishing the stable identity.
            c.execute(
                "UPDATE tp_provider SET provider_id=%s WHERE provider_sid=%s",
                (new_sid, new_sid),
            )
            conn.commit()
            ui_logger.info("Provider created", provider_id=new_sid, name=name)
            return jsonify(_row(c, new_sid)), 201
    except Exception as e:
        ui_logger.error("Provider creation failed", name=name, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@providers_bp.put("/providers/<int:pid>")
def edit_provider(pid):
    """Update a provider by creating a new SCD-2 version.

    The current row (identified by its surrogate key) is superseded
    (``current_flag = 'n'``) and a new row is inserted carrying the same
    ``provider_id`` business key at ``version + 1``.

    Args:
        pid (int): ``provider_id`` (business key) — stable across all versions.

    Returns:
        flask.Response: JSON of the updated provider row, or 400/404/409.
    """
    data = request.json or {}
    name = (data.get("provider_name") or "").strip()
    if not name:
        return jsonify({"error": "provider_name is required"}), 400

    conn = get_conn()
    try:
        with conn.cursor() as c:
            old = _row(c, pid)
            if not old:
                ui_logger.warning(
                    "Provider update rejected — not found", provider_id=pid
                )
                return jsonify({"error": "Not found"}), 404

            # Reject name collision with a *different* active provider.
            c.execute(
                "SELECT provider_id FROM tp_provider "
                "WHERE LOWER(provider_name)=LOWER(%s) AND current_flag='y' AND provider_id<>%s",
                (name, pid),
            )
            if c.fetchone():
                ui_logger.warning(
                    "Provider update rejected — duplicate name",
                    provider_id=pid,
                    name=name,
                )
                return jsonify({"error": f'Provider "{name}" already exists'}), 409

            new_version = old["version"] + 1

            # Supersede the specific version row via its surrogate key.
            c.execute(
                "UPDATE tp_provider SET current_flag='n', updated_by=%s WHERE provider_sid=%s",
                (data.get("updated_by", "system"), old["provider_sid"]),
            )
            # Insert the new version, carrying the same business key (pid).
            c.execute(
                "INSERT INTO tp_provider "
                "(provider_id, provider_name, provider_desc, provider_website, "
                "version, current_flag, created_by) VALUES (%s,%s,%s,%s,%s,'y',%s)",
                (
                    pid,
                    name,
                    data.get("provider_desc"),
                    data.get("provider_website"),
                    new_version,
                    data.get("updated_by", "system"),
                ),
            )
            conn.commit()
            ui_logger.info(
                "Provider updated", provider_id=pid, name=name, version=new_version
            )
            return jsonify(_row(c, pid))
    except Exception as e:
        ui_logger.error("Provider update failed", provider_id=pid, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@providers_bp.delete("/providers/<int:pid>")
def delete_provider(pid):
    """Logically delete a provider and cascade to its datasets and columns.

    Sets ``current_flag = 'd'`` on:
    - All version rows of this provider.
    - All version rows of every dataset owned by this provider (by business key).
    - All column rows for those datasets.

    Args:
        pid (int): ``provider_id`` (business key).

    Returns:
        flask.Response: JSON confirmation, or 404 if not found.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            old = _row(c, pid)
            if not old:
                ui_logger.warning(
                    "Provider delete rejected — not found", provider_id=pid
                )
                return jsonify({"error": "Not found"}), 404

            # Collect the distinct dataset business keys owned by this provider.
            c.execute(
                "SELECT DISTINCT dataset_id FROM tp_dataset "
                "WHERE provider_id=%s AND dataset_id IS NOT NULL",
                (pid,),
            )
            ds_bk_ids = [r["dataset_id"] for r in c.fetchall()]

            if ds_bk_ids:
                # Build a dynamic IN clause for the collected dataset business keys.
                fmt = ",".join(["%s"] * len(ds_bk_ids))
                c.execute(
                    f"UPDATE tp_dataset SET current_flag='d' WHERE dataset_id IN ({fmt})",
                    ds_bk_ids,
                )
                c.execute(
                    f"UPDATE tp_dataset_col SET current_flag='d' WHERE dataset_id IN ({fmt})",
                    ds_bk_ids,
                )

            # Mark all version rows for this provider as deleted.
            c.execute(
                "UPDATE tp_provider SET current_flag='d' WHERE provider_id=%s", (pid,)
            )
            conn.commit()
            ui_logger.info(
                "Provider deleted",
                provider_id=pid,
                name=old["provider_name"],
                cascaded_datasets=len(ds_bk_ids),
            )
            return jsonify({"message": "Deleted", "provider_id": pid})
    except Exception as e:
        ui_logger.error("Provider delete failed", provider_id=pid, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()
