# Cross-Platform Dogfood Bundle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and verify a credential-free Windows/macOS SCUDO dogfood zip.

**Architecture:** Stage a runtime-only tree from the current working copy,
including uncommitted verified fixes. Add `.cmd` and `.command` launchers that
create a local virtual environment and invoke `run_demo.py`. Generate checksums,
scan exclusions, zip, extract, and smoke-test the extracted payload.

**Tech Stack:** Python 3.11+, shell, Windows batch, ZIP, SHA-256.

---

1. Create launcher and start-guide files in a temporary staging tree.
2. Copy runtime Python modules, fixtures, prebuilt front ends, Streamlit config,
   and sample data while excluding tests, caches, local DBs, and internal docs.
3. Generate `MANIFEST.sha256`.
4. Scan for credential markers and forbidden file classes.
5. Build `SCUDO-Dogfood-2026-08-15.zip`.
6. Extract to a clean directory and verify checksums, Python compilation,
   imports, Flask health, Streamlit health, and vendored UI routes.
7. Report archive path, size, checksum, and recipient instructions.

No git commit is created unless explicitly requested.
