-- Data Ingestion Framework - console metadata schema (Aurora PostgreSQL).
-- Ported from MySQL. Consolidated onto the single Aurora cluster: the console's
-- own state lives in the `console` schema; the dynamically-created physical data
-- tables (see routes/datasets.py) live in the `ingestion` schema. db.py points
-- get_conn() at `console` and get_ingestion_conn() at `ingestion` via
-- search_path, so unqualified table names in the routes resolve correctly.
--
-- IMPORTANT: every console object below is schema-qualified (`console.<name>`).
-- Do NOT rely on `SET search_path` — RDS Data API does not retain session state
-- across ExecuteStatement calls (and even inside one transactionId the behaviour
-- is ambiguous). Unqualified CREATE + later `ALTER … SET SCHEMA` is forbidden.

CREATE SCHEMA IF NOT EXISTS console;
CREATE SCHEMA IF NOT EXISTS ingestion;

-- Replaces MySQL's `ON UPDATE CURRENT_TIMESTAMP`: a BEFORE UPDATE trigger keeps
-- updated_at current on the tables that had that clause.
CREATE OR REPLACE FUNCTION console.set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop tp_* tables in dependency order before recreating with dual-key schema.
DROP TABLE IF EXISTS console.tp_dataset_col;
DROP TABLE IF EXISTS console.tp_dataset;
DROP TABLE IF EXISTS console.tp_provider;

-- Third Party Provider (versioned SCD Type 2).
-- provider_sid : surrogate PK - unique per version row, changes on every edit.
-- provider_id  : business key - stable across all versions of the same provider.
CREATE TABLE console.tp_provider (
    provider_sid     SERIAL PRIMARY KEY,
    provider_id      INT,
    provider_name    VARCHAR(255) NOT NULL,
    provider_desc    TEXT,
    provider_website VARCHAR(500),
    version          INT NOT NULL DEFAULT 1,
    current_flag     CHAR(1) NOT NULL DEFAULT 'y',  -- y=active, n=superseded, d=deleted
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by       VARCHAR(100),
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by       VARCHAR(100),
    CONSTRAINT uq_provider_version UNIQUE (provider_id, version)
);
CREATE INDEX IF NOT EXISTS idx_provider_id   ON console.tp_provider (provider_id);
CREATE INDEX IF NOT EXISTS idx_provider_name ON console.tp_provider (provider_name);
CREATE INDEX IF NOT EXISTS idx_provider_flag ON console.tp_provider (current_flag);
CREATE TRIGGER trg_provider_updated BEFORE UPDATE ON console.tp_provider
    FOR EACH ROW EXECUTE FUNCTION console.set_updated_at();

-- Third Party Dataset (versioned SCD Type 2).
CREATE TABLE console.tp_dataset (
    dataset_sid       SERIAL PRIMARY KEY,
    dataset_id        INT,
    provider_id       INT NOT NULL,
    dataset_name      VARCHAR(255) NOT NULL,
    dataset_desc      TEXT,
    frequency         VARCHAR(50),
    data_format       VARCHAR(20),   -- csv, txt, xlsx, json, xml, parquet
    delimiter         VARCHAR(10),   -- TXT format only
    sheet_name        VARCHAR(255),  -- XLSX format only
    file_name_pattern VARCHAR(500),
    landing_folder    VARCHAR(500),
    target_table      VARCHAR(255),
    version           INT NOT NULL DEFAULT 1,
    current_flag      CHAR(1) NOT NULL DEFAULT 'y',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by        VARCHAR(100),
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by        VARCHAR(100),
    CONSTRAINT uq_dataset_version UNIQUE (dataset_id, version)
);
CREATE INDEX IF NOT EXISTS idx_dataset_id       ON console.tp_dataset (dataset_id);
CREATE INDEX IF NOT EXISTS idx_dataset_provider ON console.tp_dataset (provider_id);
CREATE INDEX IF NOT EXISTS idx_dataset_flag     ON console.tp_dataset (current_flag);
CREATE TRIGGER trg_dataset_updated BEFORE UPDATE ON console.tp_dataset
    FOR EACH ROW EXECUTE FUNCTION console.set_updated_at();

-- Third Party Dataset Column Info (versioned with dataset).
CREATE TABLE console.tp_dataset_col (
    col_id       SERIAL PRIMARY KEY,
    dataset_id   INT NOT NULL,
    col_name     VARCHAR(255) NOT NULL,
    col_type     VARCHAR(100),
    col_position INT,
    version      INT NOT NULL DEFAULT 1,
    current_flag CHAR(1) NOT NULL DEFAULT 'y',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_col_dataset_id ON console.tp_dataset_col (dataset_id);
CREATE INDEX IF NOT EXISTS idx_col_flag       ON console.tp_dataset_col (current_flag);

-- Roles.
CREATE TABLE IF NOT EXISTS console.roles (
    role_id    SERIAL PRIMARY KEY,
    role_name  VARCHAR(100) NOT NULL UNIQUE,
    role_desc  TEXT,
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Users.
CREATE TABLE IF NOT EXISTS console.users (
    user_id    SERIAL PRIMARY KEY,
    username   VARCHAR(100) NOT NULL UNIQUE,
    email      VARCHAR(255) NOT NULL UNIQUE,
    full_name  VARCHAR(255),
    password   VARCHAR(255) NOT NULL,  -- clear text - upgrade later
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User to Role Mapping.
CREATE TABLE IF NOT EXISTS console.user_roles (
    user_role_id SERIAL PRIMARY KEY,
    user_id      INT NOT NULL,
    role_id      INT NOT NULL,
    assigned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_role UNIQUE (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES console.users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES console.roles(role_id) ON DELETE CASCADE
);

-- Role Privileges (provider/dataset level add/modify/delete).
-- resource_id NULL = applies to all resources of this type.
CREATE TABLE IF NOT EXISTS console.role_privileges (
    privilege_id  SERIAL PRIMARY KEY,
    role_id       INT NOT NULL,
    resource_type VARCHAR(50) NOT NULL,  -- provider or dataset
    resource_id   INT DEFAULT NULL,
    can_add       BOOLEAN DEFAULT FALSE,
    can_modify    BOOLEAN DEFAULT FALSE,
    can_delete    BOOLEAN DEFAULT FALSE,
    CONSTRAINT uq_role_resource UNIQUE (role_id, resource_type, resource_id),
    FOREIGN KEY (role_id) REFERENCES console.roles(role_id) ON DELETE CASCADE
);

-- ETL Run Log.
CREATE TABLE IF NOT EXISTS console.etl_run_log (
    run_id          BIGSERIAL PRIMARY KEY,
    provider_id     INT          NOT NULL,
    dataset_id      INT          NOT NULL,
    run_date        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    run_status      VARCHAR(20)  NOT NULL DEFAULT 'in_progress',
    column_status   VARCHAR(30)  NOT NULL DEFAULT 'n/a',
    status_notes    TEXT,
    file_path       VARCHAR(1000),
    file_name       VARCHAR(500),
    file_size_bytes BIGINT,
    file_mtime      TIMESTAMP,
    records_found   INT,
    records_loaded  INT,
    records_skipped INT,
    target_table    VARCHAR(255),
    dataset_version INT,
    error_detail    TEXT,
    created_by      VARCHAR(100) DEFAULT 'system'
);
CREATE INDEX IF NOT EXISTS idx_run_dataset  ON console.etl_run_log (dataset_id, run_date);
CREATE INDEX IF NOT EXISTS idx_run_provider ON console.etl_run_log (provider_id, run_date);
CREATE INDEX IF NOT EXISTS idx_run_status   ON console.etl_run_log (run_status);
CREATE INDEX IF NOT EXISTS idx_run_file     ON console.etl_run_log (file_name, run_status);

-- Column-level transformation rules per dataset.
CREATE TABLE IF NOT EXISTS console.tp_dataset_col_transforms (
    transform_id  SERIAL PRIMARY KEY,
    dataset_id    INT          NOT NULL,  -- business key reference to tp_dataset.dataset_id
    col_name      VARCHAR(255) NOT NULL,
    trim_option   VARCHAR(10)  DEFAULT NULL,  -- trim | ltrim | rtrim
    null_replace  VARCHAR(500) DEFAULT NULL,  -- substitute value for NULLs; NULL = do not replace
    case_option   VARCHAR(20)  DEFAULT NULL,  -- upper | lower | sentence
    date_fmt_from VARCHAR(50)  DEFAULT NULL,  -- source strftime pattern
    date_fmt_to   VARCHAR(50)  DEFAULT NULL,  -- target strftime pattern
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_ds_col_transform UNIQUE (dataset_id, col_name)
);
CREATE INDEX IF NOT EXISTS idx_transform_dataset ON console.tp_dataset_col_transforms (dataset_id);
DROP TRIGGER IF EXISTS trg_transform_updated ON console.tp_dataset_col_transforms;
CREATE TRIGGER trg_transform_updated BEFORE UPDATE ON console.tp_dataset_col_transforms
    FOR EACH ROW EXECUTE FUNCTION console.set_updated_at();

-- Seed: default admin role and user (idempotent via ON CONFLICT DO NOTHING).
INSERT INTO console.roles (role_name, role_desc)
VALUES ('admin', 'System Administrator - full access') ON CONFLICT DO NOTHING;
INSERT INTO console.roles (role_name, role_desc)
VALUES ('viewer', 'Read-only access') ON CONFLICT DO NOTHING;

INSERT INTO console.users (username, email, full_name, password)
VALUES ('admin', 'admin@company.com', 'Administrator', 'Admin1234')
ON CONFLICT DO NOTHING;

-- Grant admin full privileges on both resources. NULL-safe idempotency: a
-- PostgreSQL UNIQUE treats NULL resource_id values as distinct, so ON CONFLICT
-- cannot dedupe these all-resource rows on re-run - guard with NOT EXISTS.
INSERT INTO console.role_privileges (role_id, resource_type, resource_id, can_add, can_modify, can_delete)
SELECT r.role_id, 'provider', NULL, TRUE, TRUE, TRUE
FROM console.roles r
WHERE r.role_name = 'admin'
  AND NOT EXISTS (
      SELECT 1 FROM console.role_privileges rp
      WHERE rp.role_id = r.role_id
        AND rp.resource_type = 'provider'
        AND rp.resource_id IS NULL
  );

INSERT INTO console.role_privileges (role_id, resource_type, resource_id, can_add, can_modify, can_delete)
SELECT r.role_id, 'dataset', NULL, TRUE, TRUE, TRUE
FROM console.roles r
WHERE r.role_name = 'admin'
  AND NOT EXISTS (
      SELECT 1 FROM console.role_privileges rp
      WHERE rp.role_id = r.role_id
        AND rp.resource_type = 'dataset'
        AND rp.resource_id IS NULL
  );

-- Map admin user to admin role.
INSERT INTO console.user_roles (user_id, role_id)
SELECT u.user_id, r.role_id FROM console.users u, console.roles r
WHERE u.username = 'admin' AND r.role_name = 'admin'
ON CONFLICT DO NOTHING;
