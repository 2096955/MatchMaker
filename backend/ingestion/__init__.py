"""Ingestion sub-package for the Data Ingestion Framework.

Exposes the public surface of the ingestion layer:

- :class:`~ingestion.engine.IngestionEngine` — abstract base class that owns
  the full orchestration pipeline (metadata loading, file discovery, column
  comparison, bulk load, log lifecycle).
- :func:`~ingestion.factory.get_ingester` — factory function that resolves the
  correct format-specific subclass for a given dataset.

Format-specific subclasses (``CsvIngester``, ``XlsxIngester``, etc.) live in
:mod:`ingestion.readers` and only implement the :meth:`read_file` abstract
method; all orchestration logic is inherited from the base class.
"""
