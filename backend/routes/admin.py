"""REST API routes for administration (``/api/roles``, ``/api/users``).

Manages two closely related areas:

**Roles** — each role has a name, optional description, active flag, and a set
of granular privileges (resource-level add/modify/delete permissions stored in
``role_privileges``).

**Users** — application users with credentials, optional full name, active
flag, and an M:N relationship to roles via ``user_roles``.

Password policy enforced by ``PWD_RE``: minimum 8 characters, at least one
uppercase letter, and at least one digit.
"""

import re
from flask import Blueprint, request, jsonify
from db import get_conn
from logger import ui_logger

admin_bp = Blueprint("admin", __name__)

# Minimum security policy: 8+ chars, one uppercase, one digit.
PWD_RE = re.compile(r"^(?=.*[A-Z])(?=.*\d).{8,}$")


# ─── ROLES ────────────────────────────────────────────────────────────────────


@admin_bp.get("/roles")
def list_roles():
    """Return all roles with their associated privilege definitions.

    Returns:
        flask.Response: JSON array of role rows, each with a ``privileges`` list
            sorted by ``resource_type, resource_id``.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT * FROM roles ORDER BY role_name")
            roles = c.fetchall()
            # Attach privilege rows to each role before returning.
            for role in roles:
                c.execute(
                    "SELECT * FROM role_privileges WHERE role_id=%s ORDER BY resource_type, resource_id",
                    (role["role_id"],),
                )
                role["privileges"] = c.fetchall()
            return jsonify(roles)
    finally:
        conn.close()


@admin_bp.get("/roles/<int:rid>")
def get_role(rid):
    """Return a single role by ID including its privileges.

    Args:
        rid (int): ``role_id`` path parameter.

    Returns:
        flask.Response: JSON role row with ``privileges`` list, or 404.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT * FROM roles WHERE role_id=%s", (rid,))
            role = c.fetchone()
            if not role:
                return jsonify({"error": "Not found"}), 404
            c.execute(
                "SELECT * FROM role_privileges WHERE role_id=%s ORDER BY resource_type, resource_id",
                (rid,),
            )
            role["privileges"] = c.fetchall()
            return jsonify(role)
    finally:
        conn.close()


@admin_bp.post("/roles")
def add_role():
    """Create a new role with optional privileges.

    Privilege rows are written via ``_save_privileges`` which inserts one row
    per privilege entry supplied in the request body.

    Returns:
        flask.Response: JSON of the created role with HTTP 201, or 400/409.
    """
    data = request.json or {}
    name = (data.get("role_name") or "").strip()
    if not name:
        return jsonify({"error": "role_name is required"}), 400

    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Reject duplicate role names (case-insensitive).
            c.execute(
                "SELECT role_id FROM roles WHERE LOWER(role_name)=LOWER(%s)", (name,)
            )
            if c.fetchone():
                ui_logger.warning("Role creation rejected — duplicate name", name=name)
                return jsonify({"error": f'Role "{name}" already exists'}), 409

            c.execute(
                "INSERT INTO roles (role_name, role_desc, is_active) VALUES (%s,%s,%s) "
                "RETURNING role_id",
                (name, data.get("role_desc"), data.get("is_active", True)),
            )
            rid = c.fetchone()["role_id"]
            # Bulk-insert privilege rows for the new role.
            _save_privileges(c, rid, data.get("privileges", []))
            conn.commit()
            # Re-fetch to return the full persisted state.
            c.execute("SELECT * FROM roles WHERE role_id=%s", (rid,))
            role = c.fetchone()
            c.execute("SELECT * FROM role_privileges WHERE role_id=%s", (rid,))
            role["privileges"] = c.fetchall()
            ui_logger.info("Role created", role_id=rid, name=name)
            return jsonify(role), 201
    except Exception as e:
        ui_logger.error("Role creation failed", name=name, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@admin_bp.put("/roles/<int:rid>")
def edit_role(rid):
    """Update a role's name, description, active flag, and privileges.

    The privilege set is replaced wholesale: all existing ``role_privileges``
    rows are deleted and the submitted list is re-inserted.

    Args:
        rid (int): ``role_id`` path parameter.

    Returns:
        flask.Response: JSON of the updated role, or 400/404/409.
    """
    data = request.json or {}
    name = (data.get("role_name") or "").strip()
    if not name:
        return jsonify({"error": "role_name is required"}), 400

    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT role_id FROM roles WHERE role_id=%s", (rid,))
            if not c.fetchone():
                ui_logger.warning("Role update rejected — not found", role_id=rid)
                return jsonify({"error": "Not found"}), 404

            # Reject name collision with a different role.
            c.execute(
                "SELECT role_id FROM roles WHERE LOWER(role_name)=LOWER(%s) AND role_id<>%s",
                (name, rid),
            )
            if c.fetchone():
                ui_logger.warning(
                    "Role update rejected — duplicate name", role_id=rid, name=name
                )
                return jsonify({"error": f'Role "{name}" already exists'}), 409

            c.execute(
                "UPDATE roles SET role_name=%s, role_desc=%s, is_active=%s WHERE role_id=%s",
                (name, data.get("role_desc"), data.get("is_active", True), rid),
            )
            # Replace privileges: delete all then re-insert the submitted list.
            c.execute("DELETE FROM role_privileges WHERE role_id=%s", (rid,))
            _save_privileges(c, rid, data.get("privileges", []))
            conn.commit()
            c.execute("SELECT * FROM roles WHERE role_id=%s", (rid,))
            role = c.fetchone()
            c.execute("SELECT * FROM role_privileges WHERE role_id=%s", (rid,))
            role["privileges"] = c.fetchall()
            ui_logger.info("Role updated", role_id=rid, name=name)
            return jsonify(role)
    except Exception as e:
        ui_logger.error("Role update failed", role_id=rid, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@admin_bp.delete("/roles/<int:rid>")
def delete_role(rid):
    """Delete a role and remove all associated privilege and user-role links.

    Args:
        rid (int): ``role_id`` path parameter.

    Returns:
        flask.Response: JSON confirmation, or 404 if not found.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT role_id, role_name FROM roles WHERE role_id=%s", (rid,))
            role_row = c.fetchone()
            if not role_row:
                ui_logger.warning("Role delete rejected — not found", role_id=rid)
                return jsonify({"error": "Not found"}), 404
            # Clean up dependent rows before deleting the role itself.
            c.execute("DELETE FROM role_privileges WHERE role_id=%s", (rid,))
            c.execute("DELETE FROM user_roles WHERE role_id=%s", (rid,))
            c.execute("DELETE FROM roles WHERE role_id=%s", (rid,))
            conn.commit()
            ui_logger.info("Role deleted", role_id=rid, name=role_row["role_name"])
            return jsonify({"message": "Deleted"})
    except Exception as e:
        ui_logger.error("Role delete failed", role_id=rid, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


def _save_privileges(c, rid, privileges):
    """Bulk-insert privilege rows for a given role.

    Each privilege entry maps a role to a resource (type + optional ID) with
    individual add/modify/delete permission flags.

    Args:
        c: Open psycopg cursor (dict rows).
        rid (int): ``role_id`` that owns the privileges.
        privileges (list[dict]): Privilege dicts with keys ``resource_type``,
            ``resource_id``, ``can_add``, ``can_modify``, ``can_delete``.
    """
    for priv in privileges:
        c.execute(
            "INSERT INTO role_privileges (role_id, resource_type, resource_id, can_add, can_modify, can_delete) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (
                rid,
                priv.get("resource_type"),
                priv.get("resource_id")
                or None,  # NULL when resource_id is not applicable
                bool(priv.get("can_add")),
                bool(priv.get("can_modify")),
                bool(priv.get("can_delete")),
            ),
        )


# ─── USERS ────────────────────────────────────────────────────────────────────


@admin_bp.get("/users")
def list_users():
    """Return all users with their assigned roles.

    Password is intentionally excluded from all query results.

    Returns:
        flask.Response: JSON array of user rows, each with a ``roles`` list.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Select only safe, non-sensitive columns.
            c.execute(
                "SELECT user_id, username, email, full_name, is_active, created_at FROM users ORDER BY username"
            )
            users = c.fetchall()
            # Attach role assignments to each user.
            for user in users:
                c.execute(
                    "SELECT r.role_id, r.role_name FROM user_roles ur "
                    "JOIN roles r ON r.role_id=ur.role_id WHERE ur.user_id=%s",
                    (user["user_id"],),
                )
                user["roles"] = c.fetchall()
            return jsonify(users)
    finally:
        conn.close()


@admin_bp.get("/users/<int:uid>")
def get_user(uid):
    """Return a single user with their role assignments by user ID.

    Args:
        uid (int): ``user_id`` path parameter.

    Returns:
        flask.Response: JSON user row with ``roles`` list, or 404.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id, username, email, full_name, is_active, created_at FROM users WHERE user_id=%s",
                (uid,),
            )
            user = c.fetchone()
            if not user:
                return jsonify({"error": "Not found"}), 404
            c.execute(
                "SELECT r.role_id, r.role_name FROM user_roles ur "
                "JOIN roles r ON r.role_id=ur.role_id WHERE ur.user_id=%s",
                (uid,),
            )
            user["roles"] = c.fetchall()
            return jsonify(user)
    finally:
        conn.close()


@admin_bp.post("/users")
def add_user():
    """Create a new application user with an initial role assignment.

    Validates that username, email, and password are all provided, that the
    password meets the ``PWD_RE`` policy, and that neither the username nor
    email already exists (case-insensitive).

    Returns:
        flask.Response: Delegates to ``get_user`` after creation (HTTP 200),
            or 400/409 on validation errors.
    """
    data = request.json or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password", "")

    if not username or not email or not password:
        ui_logger.warning(
            "User creation rejected — missing fields", username=username, email=email
        )
        return jsonify({"error": "username, email and password are required"}), 400
    if not PWD_RE.match(password):
        ui_logger.warning("User creation rejected — password policy", username=username)
        return jsonify(
            {
                "error": "Password must be at least 8 characters with one uppercase letter and one digit"
            }
        ), 400

    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Check uniqueness across both username and email in one query.
            c.execute(
                "SELECT user_id FROM users WHERE LOWER(username)=LOWER(%s) OR LOWER(email)=LOWER(%s)",
                (username, email),
            )
            if c.fetchone():
                ui_logger.warning(
                    "User creation rejected — duplicate username or email",
                    username=username,
                    email=email,
                )
                return jsonify({"error": "Username or email already exists"}), 409

            c.execute(
                "INSERT INTO users (username, email, full_name, password, is_active) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING user_id",
                (
                    username,
                    email,
                    data.get("full_name"),
                    password,
                    data.get("is_active", True),
                ),
            )
            uid = c.fetchone()["user_id"]
            # Assign the submitted roles to the new user.
            _save_user_roles(c, uid, data.get("role_ids", []))
            conn.commit()
            ui_logger.info("User created", user_id=uid, username=username)
            # Re-use the GET handler to return the full user object.
            return get_user(uid)
    except Exception as e:
        ui_logger.error("User creation failed", username=username, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@admin_bp.put("/users/<int:uid>")
def edit_user(uid):
    """Update a user's profile and role assignments.

    Password update is optional on edit — if ``password`` is omitted or empty
    the existing password hash is left unchanged.  When provided it must pass
    the ``PWD_RE`` policy check.

    Role assignments are replaced wholesale: all ``user_roles`` rows for the
    user are deleted and the submitted ``role_ids`` list is re-inserted.

    Args:
        uid (int): ``user_id`` path parameter.

    Returns:
        flask.Response: Delegates to ``get_user``, or 400/404/409.
    """
    data = request.json or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()

    if not username or not email:
        ui_logger.warning("User update rejected — missing fields", user_id=uid)
        return jsonify({"error": "username and email are required"}), 400

    password = data.get("password", "")
    if password and not PWD_RE.match(password):
        ui_logger.warning(
            "User update rejected — password policy", user_id=uid, username=username
        )
        return jsonify(
            {
                "error": "Password must be at least 8 characters with one uppercase letter and one digit"
            }
        ), 400

    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id FROM users WHERE user_id=%s", (uid,))
            if not c.fetchone():
                ui_logger.warning("User update rejected — not found", user_id=uid)
                return jsonify({"error": "Not found"}), 404

            # Reject collision with a different user sharing the same username or email.
            c.execute(
                "SELECT user_id FROM users WHERE (LOWER(username)=LOWER(%s) OR LOWER(email)=LOWER(%s)) AND user_id<>%s",
                (username, email, uid),
            )
            if c.fetchone():
                ui_logger.warning(
                    "User update rejected — duplicate username or email",
                    user_id=uid,
                    username=username,
                    email=email,
                )
                return jsonify({"error": "Username or email already exists"}), 409

            if password:
                # Update password only when a new value was explicitly provided.
                c.execute(
                    "UPDATE users SET username=%s, email=%s, full_name=%s, password=%s, is_active=%s WHERE user_id=%s",
                    (
                        username,
                        email,
                        data.get("full_name"),
                        password,
                        data.get("is_active", True),
                        uid,
                    ),
                )
            else:
                # Leave the existing password unchanged.
                c.execute(
                    "UPDATE users SET username=%s, email=%s, full_name=%s, is_active=%s WHERE user_id=%s",
                    (
                        username,
                        email,
                        data.get("full_name"),
                        data.get("is_active", True),
                        uid,
                    ),
                )

            # Replace role assignments: delete all then re-insert the submitted list.
            c.execute("DELETE FROM user_roles WHERE user_id=%s", (uid,))
            _save_user_roles(c, uid, data.get("role_ids", []))
            conn.commit()
            ui_logger.info("User updated", user_id=uid, username=username)
            return get_user(uid)
    except Exception as e:
        ui_logger.error("User update failed", user_id=uid, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


@admin_bp.delete("/users/<int:uid>")
def delete_user(uid):
    """Delete a user and remove all their role assignments.

    Args:
        uid (int): ``user_id`` path parameter.

    Returns:
        flask.Response: JSON confirmation, or 404 if not found.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id, username FROM users WHERE user_id=%s", (uid,))
            user_row = c.fetchone()
            if not user_row:
                ui_logger.warning("User delete rejected — not found", user_id=uid)
                return jsonify({"error": "Not found"}), 404
            # Remove role links before deleting the user row.
            c.execute("DELETE FROM user_roles WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM users WHERE user_id=%s", (uid,))
            conn.commit()
            ui_logger.info("User deleted", user_id=uid, username=user_row["username"])
            return jsonify({"message": "Deleted"})
    except Exception as e:
        ui_logger.error("User delete failed", user_id=uid, error=str(e))
        conn.rollback()
        raise e
    finally:
        conn.close()


def _save_user_roles(c, uid, role_ids):
    """Bulk-insert user-role mappings, silently ignoring duplicates.

    Uses ``ON CONFLICT DO NOTHING`` so calling this after a fresh DELETE is safe
    and idempotent even if the caller accidentally passes duplicate role IDs.

    Args:
        c: Open psycopg cursor (dict rows).
        uid (int): ``user_id`` that receives the role assignments.
        role_ids (list[int]): Role IDs to assign to the user.
    """
    for rid in role_ids:
        c.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (%s,%s) "
            "ON CONFLICT DO NOTHING",
            (uid, rid),
        )
