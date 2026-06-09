-- Data Ingestion Framework - Metadata Schema
-- Database: metadata

CREATE DATABASE IF NOT EXISTS ingestion;

USE metadata;

-- Drop tp_* tables in dependency order before recreating with dual-key schema
DROP TABLE IF EXISTS tp_dataset_col;
DROP TABLE IF EXISTS tp_dataset;
DROP TABLE IF EXISTS tp_provider;

-- Third Party Provider (versioned SCD Type 2)
-- provider_sid : surrogate PK — unique per version row, changes on every edit
-- provider_id  : business key — stable across all versions of the same provider
CREATE TABLE tp_provider (
    provider_sid     INT AUTO_INCREMENT PRIMARY KEY,
    provider_id      INT,
    provider_name    VARCHAR(255) NOT NULL,
    provider_desc    TEXT,
    provider_website VARCHAR(500),
    version          INT NOT NULL DEFAULT 1,
    current_flag     CHAR(1) NOT NULL DEFAULT 'y' COMMENT 'y=active, n=superseded, d=deleted',
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by       VARCHAR(100),
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by       VARCHAR(100),
    INDEX idx_provider_id   (provider_id),
    INDEX idx_provider_name (provider_name),
    INDEX idx_provider_flag (current_flag),
    UNIQUE KEY uq_provider_version (provider_id, version)
);

-- Third Party Dataset (versioned SCD Type 2)
-- dataset_sid : surrogate PK — unique per version row, changes on every edit
-- dataset_id  : business key — stable across all versions of the same dataset
-- provider_id : business key reference to tp_provider.provider_id (not provider_sid)
CREATE TABLE tp_dataset (
    dataset_sid       INT AUTO_INCREMENT PRIMARY KEY,
    dataset_id        INT,
    provider_id       INT NOT NULL,
    dataset_name      VARCHAR(255) NOT NULL,
    dataset_desc      TEXT,
    frequency         VARCHAR(50),
    data_format       VARCHAR(20) COMMENT 'csv, txt, xlsx, json, xml, parquet',
    delimiter         VARCHAR(10) COMMENT 'TXT format only',
    sheet_name        VARCHAR(255) COMMENT 'XLSX format only',
    file_name_pattern VARCHAR(500),
    landing_folder    VARCHAR(500),
    target_table      VARCHAR(255),
    version           INT NOT NULL DEFAULT 1,
    current_flag      CHAR(1) NOT NULL DEFAULT 'y' COMMENT 'y=active, n=superseded, d=deleted',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by        VARCHAR(100),
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by        VARCHAR(100),
    INDEX idx_dataset_id       (dataset_id),
    INDEX idx_dataset_provider (provider_id),
    INDEX idx_dataset_flag     (current_flag),
    UNIQUE KEY uq_dataset_version (dataset_id, version)
);

-- Third Party Dataset Column Info (versioned with dataset)
-- dataset_id : business key reference to tp_dataset.dataset_id (not dataset_sid)
CREATE TABLE tp_dataset_col (
    col_id       INT AUTO_INCREMENT PRIMARY KEY,
    dataset_id   INT NOT NULL,
    col_name     VARCHAR(255) NOT NULL,
    col_type     VARCHAR(100),
    col_position INT,
    version      INT NOT NULL DEFAULT 1,
    current_flag CHAR(1) NOT NULL DEFAULT 'y',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_col_dataset_id (dataset_id),
    INDEX idx_col_flag       (current_flag)
);

-- Roles
CREATE TABLE IF NOT EXISTS roles (
    role_id      INT AUTO_INCREMENT PRIMARY KEY,
    role_name    VARCHAR(100) NOT NULL UNIQUE,
    role_desc    TEXT,
    is_active    BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Users
CREATE TABLE IF NOT EXISTS users (
    user_id    INT AUTO_INCREMENT PRIMARY KEY,
    username   VARCHAR(100) NOT NULL UNIQUE,
    email      VARCHAR(255) NOT NULL UNIQUE,
    full_name  VARCHAR(255),
    password   VARCHAR(255) NOT NULL COMMENT 'clear text - upgrade later',
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User to Role Mapping
CREATE TABLE IF NOT EXISTS user_roles (
    user_role_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    role_id      INT NOT NULL,
    assigned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_role (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(role_id) ON DELETE CASCADE
);

-- Role Privileges (provider/dataset level add/modify/delete)
CREATE TABLE IF NOT EXISTS role_privileges (
    privilege_id  INT AUTO_INCREMENT PRIMARY KEY,
    role_id       INT NOT NULL,
    resource_type VARCHAR(50) NOT NULL COMMENT 'provider or dataset',
    resource_id   INT DEFAULT NULL COMMENT 'NULL = applies to all resources of this type',
    can_add       BOOLEAN DEFAULT FALSE,
    can_modify    BOOLEAN DEFAULT FALSE,
    can_delete    BOOLEAN DEFAULT FALSE,
    UNIQUE KEY uq_role_resource (role_id, resource_type, resource_id),
    FOREIGN KEY (role_id) REFERENCES roles(role_id) ON DELETE CASCADE
);

-- ETL Run Log
CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    provider_id     INT          NOT NULL,
    dataset_id      INT          NOT NULL,
    run_date        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    run_status      VARCHAR(20)  NOT NULL DEFAULT 'in_progress'
                    COMMENT 'in_progress | success | failed | partial | skipped',
    column_status   VARCHAR(30)  NOT NULL DEFAULT 'n/a'
                    COMMENT 'match | extra_cols_in_file | missing_cols_in_file | col_mismatch | n/a',
    status_notes    TEXT,
    file_path       VARCHAR(1000),
    file_name       VARCHAR(500),
    file_size_bytes BIGINT,
    file_mtime      DATETIME,
    records_found   INT,
    records_loaded  INT,
    records_skipped INT,
    target_table    VARCHAR(255),
    dataset_version INT,
    error_detail    TEXT,
    created_by      VARCHAR(100) DEFAULT 'system',
    INDEX idx_run_dataset  (dataset_id, run_date),
    INDEX idx_run_provider (provider_id, run_date),
    INDEX idx_run_status   (run_status),
    INDEX idx_run_file     (file_name, run_status)
);

-- Column-level transformation rules per dataset
CREATE TABLE IF NOT EXISTS tp_dataset_col_transforms (
    transform_id  INT AUTO_INCREMENT PRIMARY KEY,
    dataset_id    INT          NOT NULL COMMENT 'business key reference to tp_dataset.dataset_id',
    col_name      VARCHAR(255) NOT NULL,
    trim_option   VARCHAR(10)  DEFAULT NULL COMMENT 'trim | ltrim | rtrim',
    null_replace  VARCHAR(500) DEFAULT NULL COMMENT 'substitute value for NULLs; NULL = do not replace',
    case_option   VARCHAR(20)  DEFAULT NULL COMMENT 'upper | lower | sentence',
    date_fmt_from VARCHAR(50)  DEFAULT NULL COMMENT 'source strftime pattern  e.g. %d/%m/%Y',
    date_fmt_to   VARCHAR(50)  DEFAULT NULL COMMENT 'target strftime pattern  e.g. %Y-%m-%d',
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ds_col_transform (dataset_id, col_name),
    INDEX idx_transform_dataset (dataset_id)
);

-- Seed: default admin role and user
INSERT IGNORE INTO roles (role_name, role_desc) VALUES ('admin', 'System Administrator - full access');
INSERT IGNORE INTO roles (role_name, role_desc) VALUES ('viewer', 'Read-only access');

INSERT IGNORE INTO users (username, email, full_name, password)
VALUES ('admin', 'admin@company.com', 'Administrator', 'Admin1234');

-- Grant admin full privileges on both resources
INSERT IGNORE INTO role_privileges (role_id, resource_type, resource_id, can_add, can_modify, can_delete)
SELECT r.role_id, 'provider', NULL, TRUE, TRUE, TRUE FROM roles r WHERE r.role_name = 'admin';

INSERT IGNORE INTO role_privileges (role_id, resource_type, resource_id, can_add, can_modify, can_delete)
SELECT r.role_id, 'dataset', NULL, TRUE, TRUE, TRUE FROM roles r WHERE r.role_name = 'admin';

-- Map admin user to admin role
INSERT IGNORE INTO user_roles (user_id, role_id)
SELECT u.user_id, r.role_id FROM users u, roles r WHERE u.username='admin' AND r.role_name='admin';
