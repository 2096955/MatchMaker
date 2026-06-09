"""Factory for resolving format-specific ingestion engine instances.

Maintains a registry mapping ``data_format`` strings (as stored in
``tp_dataset.data_format``) to their corresponding
:class:`~ingestion.engine.IngestionEngine` subclasses.  Callers use
:func:`get_ingester` as the sole entry point — they never instantiate
subclasses directly.

To add support for a new format:

1. Implement a new subclass in :mod:`ingestion.readers`.
2. Import it here and add it to :data:`_REGISTRY`.
"""

from db import get_conn
from ingestion.readers import (
    CsvIngester, TxtIngester, JsonIngester,
    XlsxIngester, XmlIngester, ParquetIngester,
)

# Maps data_format values (lower-cased) to their ingestion engine subclasses.
_REGISTRY = {
    'csv':     CsvIngester,
    'txt':     TxtIngester,
    'json':    JsonIngester,
    'xlsx':    XlsxIngester,
    'xml':     XmlIngester,
    'parquet': ParquetIngester,
}


def get_ingester(dataset_id):
    """Resolve and instantiate the correct ingestion engine for a dataset.

    Looks up the ``data_format`` of the dataset from ``tp_dataset``, finds the
    matching engine class in ``_REGISTRY``, and returns an initialised instance.

    Args:
        dataset_id (int): ``dataset_id`` business key from ``tp_dataset``.

    Returns:
        IngestionEngine: An instance of the format-specific subclass, ready
            for ``.run()`` to be called.

    Raises:
        ValueError: If the dataset does not exist or its format is not
            supported by any registered engine.
    """
    conn = get_conn()
    try:
        with conn.cursor() as c:
            # Fetch only the data_format field — full metadata is loaded by the engine itself.
            c.execute(
                "SELECT data_format FROM tp_dataset "
                "WHERE dataset_id=%s AND current_flag='y'",
                (dataset_id,),
            )
            row = c.fetchone()
    finally:
        conn.close()

    if not row:
        raise ValueError(f'Dataset {dataset_id} not found')

    fmt = (row.get('data_format') or '').lower()
    cls = _REGISTRY.get(fmt)
    if not cls:
        raise ValueError(f'Unsupported format: {fmt!r}')
    return cls(dataset_id)
