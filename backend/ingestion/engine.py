"""Core ingestion engine for the Data Ingestion Framework.

Defines :class:`IngestionEngine`, an abstract base class that owns the entire
ETL orchestration pipeline for a single dataset.  Format-specific subclasses
(CSV, XLSX, JSON, etc.) inherit from this class and implement only the
:meth:`read_file` method — all other logic is shared.

Pipeline (executed per-file inside :meth:`run`):

1. Load dataset/column metadata from ``tp_dataset`` + ``tp_dataset_col``.
2. Discover files in the landing folder matching ``file_name_pattern``.
3. For each file: create an ``in_progress`` log stub in ``etl_run_log``.
4. Read the file into a pandas DataFrame via the subclass ``read_file``.
5. Apply column-level transformations (trim / null-replace / case / date fmt).
6. Compare DataFrame columns against the registered dataset columns.
7. Bulk-insert the intersection of known columns into the target table.
8. Move the processed file to ``landing_folder/processed/YYYYMMDD_HHMMSS/``.
9. Update the log stub with the final status, counts, and any notes.
"""

import os
import glob
import shutil
import traceback
from datetime import datetime
import pandas as pd
from db import get_conn, get_ingestion_conn
from logger import ingestion_logger


class IngestionEngine:
    """Abstract base class for format-specific ingestion engines.

    Owns the full ETL pipeline.  Subclasses only need to implement
    :meth:`read_file`, which receives a file path and must return a
    ``pd.DataFrame``.

    Attributes:
        dataset_id (int): Business key of the dataset being ingested.
        meta (dict | None): Populated by :meth:`_load_metadata` before any
            file processing begins.  Contains all ``tp_dataset`` columns plus
            ``provider_name`` and a ``columns`` list from ``tp_dataset_col``.
    """

    def __init__(self, dataset_id):
        """Initialise the engine for a specific dataset.

        Args:
            dataset_id (int): ``dataset_id`` business key from ``tp_dataset``.
        """
        self.dataset_id = dataset_id
        self.meta = None

    # ── Public entry point ───────────────────────────────────────────────

    def run(self):
        """Execute the ingestion pipeline for the dataset.

        Loads metadata, discovers eligible files, and processes each file
        sequentially.  When no files are found a single ``skipped`` log entry
        is returned.

        Returns:
            list[dict]: One ``etl_run_log`` dict per file processed (or one
                ``skipped`` entry if no files matched).

        Raises:
            ValueError: If the dataset is not found or is inactive.
        """
        ingestion_logger.info("Ingestion run started", dataset_id=self.dataset_id)

        self._load_metadata()
        files = self._discover_files()

        ingestion_logger.info(
            "File discovery complete",
            dataset_id=self.dataset_id,
            dataset=self.meta["dataset_name"],
            provider=self.meta["provider_name"],
            folder=self.meta.get("landing_folder"),
            pattern=self.meta.get("file_name_pattern"),
            files_found=len(files),
        )

        if not files:
            # No files found — log a single skipped entry and return.
            run_id = self._create_log_stub(None)
            self._update_log(
                run_id,
                run_status="skipped",
                column_status="n/a",
                status_notes="No files found matching pattern in landing folder",
                records_found=0,
                records_loaded=0,
                records_skipped=0,
            )
            ingestion_logger.warning("No files found — run logged as skipped", dataset_id=self.dataset_id, run_id=run_id)
            return [self._get_log(run_id)]

        results = [self._process_file(fp) for fp in files]

        # Summarise outcomes across all processed files.
        summary: dict = {}
        for r in results:
            if r:
                key = r.get("run_status", "unknown")
                summary[key] = summary.get(key, 0) + 1
        ingestion_logger.info("Ingestion run completed", dataset_id=self.dataset_id, total_files=len(files), **summary)
        return results

    # ── Abstract — subclasses implement ─────────────────────────────────

    def read_file(self, file_path):
        """Read a source file and return its contents as a DataFrame.

        Subclasses must override this method.  They may use ``self.meta``
        (e.g. ``self.meta.get('delimiter')``) to customise the reader.

        Args:
            file_path (str): Absolute path to the source file.

        Returns:
            pd.DataFrame: Raw data from the file with column names preserved.

        Raises:
            NotImplementedError: Always — must be overridden by a subclass.
        """
        raise NotImplementedError

    # ── Orchestration ────────────────────────────────────────────────────

    def _load_metadata(self):
        """Fetch dataset configuration and column definitions from the metadata DB.

        Populates ``self.meta`` with the ``tp_dataset`` row (joined to
        ``tp_provider`` for ``provider_name``) plus a ``columns`` list from
        ``tp_dataset_col``.

        Raises:
            ValueError: If the dataset does not exist or is not active.
        """
        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(
                    "SELECT d.*, p.provider_name FROM tp_dataset d "
                    "JOIN tp_provider p ON p.provider_id=d.provider_id AND p.current_flag='y' "
                    "WHERE d.dataset_id=%s AND d.current_flag='y'",
                    (self.dataset_id,),
                )
                row = c.fetchone()
                if not row:
                    ingestion_logger.error("Dataset not found or inactive", dataset_id=self.dataset_id)
                    raise ValueError(f"Dataset {self.dataset_id} not found or not active")
                # Attach column definitions to the metadata dict.
                c.execute(
                    "SELECT col_name, col_type, col_position FROM tp_dataset_col "
                    "WHERE dataset_id=%s AND current_flag='y' ORDER BY col_position",
                    (self.dataset_id,),
                )
                row["columns"] = c.fetchall()
                self.meta = row
        finally:
            conn.close()

        ingestion_logger.debug(
            "Metadata loaded",
            dataset_id=self.dataset_id,
            dataset=self.meta["dataset_name"],
            provider=self.meta["provider_name"],
            version=self.meta.get("version"),
            columns=len(self.meta["columns"]),
            target_table=self.meta.get("target_table"),
        )

    def _discover_files(self):
        """Glob the landing folder for files matching the configured pattern.

        Files inside the ``processed/`` sub-directory are excluded so that
        already-moved files are never picked up on a re-run.  Results are
        sorted by modification time (oldest first) so files are loaded in
        the order they arrived.

        Returns:
            list[str]: Absolute paths of eligible files, sorted by mtime.
        """
        folder = (self.meta.get("landing_folder") or "").strip()
        pattern = (self.meta.get("file_name_pattern") or "*").strip()
        if not folder or not os.path.isdir(folder):
            ingestion_logger.warning("Landing folder not found or not a directory", dataset_id=self.dataset_id, folder=folder)
            return []
        matches = glob.glob(os.path.join(folder, pattern))
        # Exclude the processed sub-directory using absolute-path comparison.
        proc = os.path.join(folder, "processed")
        matches = [m for m in matches if not os.path.abspath(m).startswith(os.path.abspath(proc))]
        return sorted(matches, key=os.path.getmtime)

    def _already_processed(self, file_path):
        """Check whether the file was previously loaded successfully.

        Deduplication key: ``(dataset_id, file_name, file_size_bytes)`` with
        ``run_status = 'success'``.  Using name + size (not mtime) means a
        re-delivered file with identical content is safely skipped, while a
        file with the same name but different size is treated as new data.

        Args:
            file_path (str): Absolute path to the candidate file.

        Returns:
            dict | None: The most recent successful log row, or ``None`` if the
                file has not been processed before.
        """
        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(
                    "SELECT run_id, run_date FROM etl_run_log "
                    "WHERE dataset_id=%s AND file_name=%s AND file_size_bytes=%s "
                    "AND run_status='success' ORDER BY run_date DESC LIMIT 1",
                    (self.dataset_id, os.path.basename(file_path), os.path.getsize(file_path)),
                )
                return c.fetchone()
        finally:
            conn.close()

    def _process_file(self, file_path):
        """Orchestrate ingestion of a single file end-to-end.

        Steps:

        1. Stat the file (size, mtime).
        2. Skip if already processed (log ``skipped``; attempt move anyway).
        3. Create an ``in_progress`` log stub to allow live progress polling.
        4. Read the file via the subclass ``read_file``.
        5. Apply column-level transformations.
        6. Compare columns; fail if no known columns are present.
        7. Bulk-load the intersection of known columns into the target table.
        8. Move the file to the processed archive directory.
        9. Update the log stub with final status and record counts.

        Args:
            file_path (str): Absolute path to the file to process.

        Returns:
            dict: Final ``etl_run_log`` row for this file.
        """
        fname = os.path.basename(file_path)
        fsize = os.path.getsize(file_path)
        fmtime = datetime.fromtimestamp(os.path.getmtime(file_path))

        ingestion_logger.info("File processing started", dataset_id=self.dataset_id, file=fname, size_bytes=fsize, mtime=str(fmtime))

        prior = self._already_processed(file_path)
        if prior:
            ingestion_logger.warning(
                "File already processed — skipping",
                dataset_id=self.dataset_id,
                file=fname,
                prior_run_id=prior["run_id"],
                prior_date=str(prior["run_date"]),
            )

            run_id = self._create_log_stub(file_path, fsize, fmtime)
            self._update_log(
                run_id,
                run_status="skipped",
                column_status="n/a",
                status_notes=f'Already processed on {prior["run_date"]}',
                records_found=0,
                records_loaded=0,
                records_skipped=0,
            )
            try:
                # Attempt to move the duplicate out of the landing folder.
                dest = self._move_to_processed(file_path)
                ingestion_logger.debug("Duplicate file archived", file=fname, dest=dest)
            except Exception:
                pass  # Move failure on a skip is not critical.
            return self._get_log(run_id)

        # Create the log stub immediately so polling can detect this in-progress run.
        run_id = self._create_log_stub(file_path, fsize, fmtime)
        ingestion_logger.debug("Log stub created", run_id=run_id, file=fname)

        try:
            df = self.read_file(file_path)
            ingestion_logger.debug("File read into DataFrame", run_id=run_id, file=fname, raw_records=len(df), columns=len(df.columns))

            # Apply column-level transformations before schema comparison.
            df = self.do_transformations(df)
            records_found = len(df)

            # Determine which columns to load based on the registered schema.
            cmp = self._compare_columns(list(df.columns))
            ingestion_logger.info(
                "Column comparison result", run_id=run_id, file=fname, status=cmp["status"], loadable_columns=len(cmp["load_columns"])
            )
            if cmp["notes"]:
                ingestion_logger.warning("Column discrepancy noted", run_id=run_id, file=fname, notes=cmp["notes"])

            if not cmp["load_columns"]:
                # No overlap between file columns and registered columns — cannot load.
                ingestion_logger.error("No loadable columns — run marked failed", run_id=run_id, file=fname, column_status=cmp["status"])
                self._update_log(
                    run_id,
                    run_status="failed",
                    column_status=cmp["status"],
                    status_notes=f'No known columns to load. {cmp["notes"] or ""}',
                    records_found=records_found,
                    records_loaded=0,
                    records_skipped=records_found,
                )
                return self._get_log(run_id)

            loaded, skipped = self._load_to_target(df, cmp["load_columns"], run_id)
            ingestion_logger.info(
                "Rows loaded to target table",
                run_id=run_id,
                file=fname,
                table=self.meta.get("target_table"),
                loaded=loaded,
                skipped=skipped,
            )

            # Attempt to archive the processed file; capture failure as a note.
            move_note = None
            try:
                dest = self._move_to_processed(file_path)
                ingestion_logger.debug("File archived to processed folder", file=fname, dest=dest)
            except Exception as e:
                move_note = f"Move failed: {e}"
                ingestion_logger.warning("File archive failed", run_id=run_id, file=fname, error=str(e))

            # Mark as partial if any rows were skipped or the file move failed.
            final_status = "success"
            if skipped > 0 or move_note:
                final_status = "partial"

            # Combine column-comparison notes and move-failure notes into one string.
            notes_parts = [p for p in [cmp["notes"], move_note] if p]
            self._update_log(
                run_id,
                run_status=final_status,
                column_status=cmp["status"],
                status_notes=" | ".join(notes_parts) if notes_parts else None,
                records_found=records_found,
                records_loaded=loaded,
                records_skipped=skipped,
            )

            ingestion_logger.info(
                "File processing complete",
                run_id=run_id,
                file=fname,
                status=final_status,
                records_found=records_found,
                loaded=loaded,
                skipped=skipped,
            )

        except Exception as e:
            # Capture the full traceback in error_detail for debugging.
            tb = traceback.format_exc()
            ingestion_logger.error("File processing failed with unhandled exception", run_id=run_id, file=fname, error=str(e), traceback=tb)
            self._update_log(
                run_id,
                run_status="failed",
                column_status="n/a",
                status_notes=str(e),
                error_detail=tb,
                records_found=0,
                records_loaded=0,
                records_skipped=0,
            )

        return self._get_log(run_id)

    def _compare_columns(self, file_columns):
        """Compare the file's columns against the registered dataset schema.

        Four outcomes are possible:

        - ``match``               — File columns exactly equal registered columns.
        - ``extra_cols_in_file``  — File has columns not in the schema (ignored).
        - ``missing_cols_in_file``— Schema has columns absent from the file (loaded as NULL).
        - ``col_mismatch``        — Both extra and missing columns are present.

        Only the intersection (``common``) is returned as ``load_columns`` so
        the INSERT only targets columns known to both the schema and the file.

        Args:
            file_columns (list[str]): Column names as returned by the reader.

        Returns:
            dict: Keys ``status`` (str), ``notes`` (str | None), and
                ``load_columns`` (list[str]) — all lower-cased.
        """
        known = {c["col_name"].lower() for c in self.meta["columns"]}
        actual = {c.lower() for c in file_columns}
        extra = sorted(actual - known)  # In file but not in schema — will be ignored.
        missing = sorted(known - actual)  # In schema but not in file — will be NULL.
        common = sorted(known & actual)  # Intersection — the columns we will load.

        if not extra and not missing:
            status, notes = "match", None
        elif extra and not missing:
            status = "extra_cols_in_file"
            notes = f"Extra columns ignored: {extra}"
        elif missing and not extra:
            status = "missing_cols_in_file"
            notes = f"Missing columns loaded as NULL: {missing}"
        else:
            status = "col_mismatch"
            notes = f"Extra (ignored): {extra} | Missing (NULL): {missing}"

        return {"status": status, "notes": notes, "load_columns": common}

    def _load_to_target(self, df, load_columns, run_id):
        """Bulk-insert data rows into the dataset's physical ingestion table.

        Builds a column-aligned subset of the DataFrame, appends the five
        mandatory metadata columns, and inserts rows one by one (row-level
        error isolation — a bad row is counted as skipped, not a fatal error).

        Args:
            df (pd.DataFrame): Full DataFrame returned by ``read_file``.
            load_columns (list[str]): Lower-cased column names in the intersection
                of file and schema columns.
            run_id (int): Current ``etl_run_log.run_id`` used as ``load_id``.

        Returns:
            tuple[int, int]: ``(loaded, skipped)`` counts.

        Raises:
            ValueError: If the dataset has no ``target_table`` configured.
        """
        target_table = self.meta.get("target_table")
        if not target_table:
            ingestion_logger.error("No target_table configured — cannot load", dataset_id=self.dataset_id, run_id=run_id)
            raise ValueError("Dataset has no target_table configured")

        # Map lower-cased column names back to their original casing in the DataFrame.
        col_map = {c.lower(): c for c in df.columns}
        actual_cols = [col_map[lc] for lc in load_columns if lc in col_map]

        df_load = df[actual_cols].copy()
        # Normalise all column names to lower case to match the DDL.
        df_load.columns = [c.lower() for c in df_load.columns]

        # Insert NULL for any registered columns that were absent in the file.
        known_cols = [c["col_name"].lower() for c in self.meta["columns"]]
        for kc in known_cols:
            if kc not in df_load.columns:
                df_load[kc] = None

        # Append the five mandatory audit/metadata columns.
        df_load["load_id"] = run_id
        df_load["provider_id"] = self.meta["provider_id"]
        df_load["dataset_id"] = self.meta["dataset_id"]
        df_load["load_date"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        df_load["load_process"] = f"dataset_id:{self.dataset_id}|file:{os.path.basename(self.meta.get('file_name_pattern',''))}"

        # Replace pandas NaN/NaT with Python None so pymysql sends SQL NULL.
        df_load = df_load.where(pd.notnull(df_load), None)

        # Build the parameterised INSERT statement once for all rows.
        cols = list(df_load.columns)
        backticked = ", ".join([f"`{c}`" for c in cols])
        placeholders = ", ".join(["%s"] * len(cols))
        sql = f"INSERT INTO `{target_table}` ({backticked}) VALUES ({placeholders})"

        # Convert DataFrame rows to plain tuples for pymysql.
        rows = [tuple(r) for r in df_load.itertuples(index=False, name=None)]

        ingestion_logger.debug("Starting bulk insert", run_id=run_id, table=target_table, total_rows=len(rows))

        loaded = skipped = 0
        ing = get_ingestion_conn()
        try:
            with ing.cursor() as ic:
                for row in rows:
                    try:
                        ic.execute(sql, row)
                        loaded += 1
                    except Exception as row_err:
                        # Row-level isolation: count the failure and continue.
                        skipped += 1
                        ingestion_logger.debug("Row insert failed", run_id=run_id, table=target_table, error=str(row_err))
                ing.commit()
        finally:
            ing.close()

        return loaded, skipped

    def _move_to_processed(self, file_path):
        """Move a processed file to a timestamped archive sub-directory.

        The destination path is ``{landing_folder}/processed/YYYYMMDD_HHMMSS/``.
        A unique timestamp per call means concurrent runs never collide on the
        destination directory.

        Args:
            file_path (str): Absolute path to the file to move.

        Returns:
            str: Absolute path of the file at its new location.

        Raises:
            Exception: Any ``shutil.move`` or ``os.makedirs`` failure is
                propagated; callers should catch and record it as a note.
        """
        folder = (self.meta.get("landing_folder") or "").strip()
        # Timestamp directory isolates each run's processed files.
        ts_dir = os.path.join(folder, "processed", datetime.utcnow().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(ts_dir, exist_ok=True)
        dest = os.path.join(ts_dir, os.path.basename(file_path))
        shutil.move(file_path, dest)
        return dest

    # ── Transformations ──────────────────────────────────────────────────

    def _load_transforms(self):
        """Load transformation rules for the dataset from ``tp_dataset_col_transforms``.

        Returns:
            dict[str, dict]: Mapping of lower-cased column name → transform row.
                Empty dict when no rules have been configured.
        """
        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(
                    "SELECT col_name, trim_option, null_replace, case_option, "
                    "date_fmt_from, date_fmt_to "
                    "FROM tp_dataset_col_transforms WHERE dataset_id=%s",
                    (self.dataset_id,),
                )
                return {r["col_name"].lower(): r for r in c.fetchall()}
        finally:
            conn.close()

    def do_transformations(self, df):
        """Apply configured column-level transformations to a DataFrame.

        Transformations are applied per column in this fixed order:

        1. **Trim** — strip leading/trailing whitespace (trim / ltrim / rtrim).
        2. **Null replace** — substitute a literal value for ``NaN`` / ``None``.
        3. **Case** — convert string values to upper, lower, or sentence case.
        4. **Date format** — parse with ``date_fmt_from`` and reformat to
           ``date_fmt_to`` (both must be set; failures are silenced per-column).

        Only columns that have at least one rule in ``tp_dataset_col_transforms``
        are touched; all others pass through unchanged.

        Args:
            df (pd.DataFrame): Raw DataFrame returned by ``read_file``.

        Returns:
            pd.DataFrame: Transformed copy of the input DataFrame.
        """
        transforms = self._load_transforms()
        if not transforms:
            ingestion_logger.debug("No transformations configured", dataset_id=self.dataset_id)
            return df

        ingestion_logger.debug("Applying transformations", dataset_id=self.dataset_id, transform_columns=list(transforms.keys()))

        df = df.copy()
        # Build a case-insensitive map from lower-cased name → actual DataFrame column.
        col_map = {c.lower(): c for c in df.columns}
        applied: list[str] = []

        for lc_name, t in transforms.items():
            actual = col_map.get(lc_name)
            if actual is None:
                continue  # Column in transform table but not in this file — skip.

            series = df[actual]
            ops: list[str] = []

            # 1. Trim — pandas str methods preserve NaN, so no special handling needed.
            trim = t.get("trim_option")
            if trim:
                if trim == "trim":
                    series = series.str.strip()
                elif trim == "ltrim":
                    series = series.str.lstrip()
                elif trim == "rtrim":
                    series = series.str.rstrip()
                ops.append(f"trim={trim}")

            # 2. Null replacement — applies to all column types.
            null_rep = t.get("null_replace")
            if null_rep is not None:
                series = series.fillna(null_rep)
                ops.append(f"null_replace={null_rep!r}")

            # 3. Case — str methods return NaN for NaN elements, so safe on mixed series.
            case = t.get("case_option")
            if case:
                if case == "upper":
                    series = series.str.upper()
                elif case == "lower":
                    series = series.str.lower()
                elif case == "sentence":
                    series = series.str.capitalize()
                ops.append(f"case={case}")

            # 4. Date format conversion — both from and to must be configured.
            date_from = t.get("date_fmt_from")
            date_to = t.get("date_fmt_to")
            if date_from and date_to:
                try:
                    # errors='coerce' turns unparseable values into NaT rather than raising.
                    series = pd.to_datetime(series, format=date_from, errors="coerce").dt.strftime(date_to)
                    ops.append(f"date_fmt={date_from!r}->{date_to!r}")
                except Exception as e:
                    ingestion_logger.warning(
                        "Date format conversion failed for column",
                        dataset_id=self.dataset_id,
                        column=actual,
                        date_from=date_from,
                        date_to=date_to,
                        error=str(e),
                    )

            df[actual] = series
            if ops:
                applied.append(f'{actual}({", ".join(ops)})')

        if applied:
            ingestion_logger.info("Transformations applied", dataset_id=self.dataset_id, columns=applied)

        return df

    # ── Log helpers ──────────────────────────────────────────────────────

    def _create_log_stub(self, file_path, fsize=None, fmtime=None):
        """Insert an ``in_progress`` placeholder row in ``etl_run_log``.

        Called at the very start of processing each file so that the frontend
        polling loop can find the run ID and display live progress before the
        POST response arrives.

        Args:
            file_path (str | None): Absolute path to the file being processed,
                or ``None`` when no files were found.
            fsize (int | None): File size in bytes.
            fmtime (datetime | None): File modification timestamp.

        Returns:
            int: The ``run_id`` (auto-increment primary key) of the new row.
        """
        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO etl_run_log "
                    "(provider_id, dataset_id, file_path, file_name, file_size_bytes, "
                    "file_mtime, target_table, dataset_version, run_status, column_status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'in_progress','n/a')",
                    (
                        self.meta["provider_id"],
                        self.meta["dataset_id"],
                        file_path,
                        os.path.basename(file_path) if file_path else None,
                        fsize,
                        fmtime,
                        self.meta.get("target_table"),
                        self.meta.get("version"),
                    ),
                )
                run_id = c.lastrowid
                conn.commit()
                ingestion_logger.debug(
                    "Run log stub created",
                    run_id=run_id,
                    dataset_id=self.dataset_id,
                    file=os.path.basename(file_path) if file_path else None,
                )
                return run_id
        finally:
            conn.close()

    def _update_log(self, run_id, **fields):
        """Update arbitrary columns on an existing ``etl_run_log`` row.

        Builds the SET clause dynamically from keyword arguments, allowing
        callers to set only the fields that are relevant at each stage of
        processing.

        Args:
            run_id (int): Primary key of the log row to update.
            **fields: Column=value pairs to update (e.g.
                ``run_status='success'``, ``records_loaded=500``).
        """
        if not fields:
            return
        conn = get_conn()
        try:
            with conn.cursor() as c:
                set_clause = ", ".join([f"{k}=%s" for k in fields])
                vals = list(fields.values()) + [run_id]
                c.execute(f"UPDATE etl_run_log SET {set_clause} WHERE run_id=%s", vals)
                conn.commit()
        finally:
            conn.close()

        status = fields.get("run_status")
        if status:
            ingestion_logger.debug(
                "Run log updated",
                run_id=run_id,
                run_status=status,
                records_loaded=fields.get("records_loaded"),
                records_skipped=fields.get("records_skipped"),
            )

    def _get_log(self, run_id):
        """Fetch a single ``etl_run_log`` row by run ID.

        Used to return the final log state to the caller after processing is
        complete.

        Args:
            run_id (int): Primary key of the log row.

        Returns:
            dict | None: The log row as a dict, or ``None`` if not found.
        """
        conn = get_conn()
        try:
            with conn.cursor() as c:
                c.execute("SELECT * FROM etl_run_log WHERE run_id=%s", (run_id,))
                return c.fetchone()
        finally:
            conn.close()
