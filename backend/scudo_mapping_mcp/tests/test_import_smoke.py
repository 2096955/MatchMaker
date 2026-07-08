"""Import smoke — catches missing top-level imports on P0-touched modules."""

from __future__ import annotations


def test_import_retrieval_memory_store_tools():
    import scudo.tools  # noqa: F401
    import scudo_mapping_mcp.retrieval  # noqa: F401
    import scudo_mapping_mcp.store.memory_store  # noqa: F401


def test_import_p0_loader_surface():
    import scudo_mapping_mcp.csvw_aliases  # noqa: F401
    from scudo_mapping_mcp.csvw_aliases import csvw_metadata_from_rdf  # noqa: F401
    import scudo_mapping_mcp.ingest  # noqa: F401
    import scudo_mapping_mcp.loaders.dcat_loader  # noqa: F401
    import scudo_mapping_mcp.loaders.taxonomy_loader  # noqa: F401
    import scudo_mapping_mcp.taxonomy_text  # noqa: F401
    import scudo_mapping_mcp.turtle_ingester  # noqa: F401
