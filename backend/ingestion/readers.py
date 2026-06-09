"""Format-specific ingestion engine subclasses.

Each class inherits all orchestration logic from :class:`~ingestion.engine.IngestionEngine`
and implements only the :meth:`read_file` abstract method.  Adding support for
a new file format requires:

1. Creating a new subclass here.
2. Registering it in :data:`~ingestion.factory._REGISTRY`.

Available readers:

- :class:`CsvIngester`     — comma-separated values (standard CSV).
- :class:`TxtIngester`     — delimiter-separated text; delimiter read from
  ``self.meta['delimiter']``, defaults to comma if not configured.
- :class:`JsonIngester`    — JSON array or records format.
- :class:`XlsxIngester`   — Excel workbook; ``self.meta['sheet_name']``
  selects the sheet (defaults to the first sheet when not set).
- :class:`XmlIngester`    — XML document parsed by ``pandas.read_xml``.
- :class:`ParquetIngester``— Apache Parquet columnar format.
"""

import pandas as pd
from ingestion.engine import IngestionEngine


class CsvIngester(IngestionEngine):
    """Ingestion engine for comma-separated value (CSV) files."""

    def read_file(self, file_path):
        """Read a CSV file using pandas defaults (comma delimiter, header row 0).

        Args:
            file_path (str): Absolute path to the ``.csv`` file.

        Returns:
            pd.DataFrame: Parsed file contents.
        """
        return pd.read_csv(file_path)


class TxtIngester(IngestionEngine):
    """Ingestion engine for delimiter-separated text files.

    The delimiter is taken from ``self.meta['delimiter']``; falls back to a
    comma when the field is empty or not configured.
    """

    def read_file(self, file_path):
        """Read a delimited text file using the dataset's configured delimiter.

        Args:
            file_path (str): Absolute path to the ``.txt`` file.

        Returns:
            pd.DataFrame: Parsed file contents.
        """
        delimiter = self.meta.get('delimiter') or ','
        return pd.read_csv(file_path, sep=delimiter)


class JsonIngester(IngestionEngine):
    """Ingestion engine for JSON files (array or records orientation)."""

    def read_file(self, file_path):
        """Read a JSON file via ``pandas.read_json``.

        Args:
            file_path (str): Absolute path to the ``.json`` file.

        Returns:
            pd.DataFrame: Parsed file contents.
        """
        return pd.read_json(file_path)


class XlsxIngester(IngestionEngine):
    """Ingestion engine for Excel workbook files (``.xlsx``).

    The worksheet is selected via ``self.meta['sheet_name']``; defaults to the
    first sheet (index ``0``) when not configured.
    """

    def read_file(self, file_path):
        """Read an Excel workbook using the dataset's configured sheet name.

        Args:
            file_path (str): Absolute path to the ``.xlsx`` file.

        Returns:
            pd.DataFrame: Parsed worksheet contents.
        """
        sheet = self.meta.get('sheet_name') or 0
        return pd.read_excel(file_path, sheet_name=sheet)


class XmlIngester(IngestionEngine):
    """Ingestion engine for XML files parsed by ``pandas.read_xml``."""

    def read_file(self, file_path):
        """Read an XML file via ``pandas.read_xml``.

        Args:
            file_path (str): Absolute path to the ``.xml`` file.

        Returns:
            pd.DataFrame: Parsed file contents.
        """
        return pd.read_xml(file_path)


class ParquetIngester(IngestionEngine):
    """Ingestion engine for Apache Parquet columnar files."""

    def read_file(self, file_path):
        """Read a Parquet file via ``pandas.read_parquet``.

        Args:
            file_path (str): Absolute path to the ``.parquet`` file.

        Returns:
            pd.DataFrame: Parsed file contents.
        """
        return pd.read_parquet(file_path)
