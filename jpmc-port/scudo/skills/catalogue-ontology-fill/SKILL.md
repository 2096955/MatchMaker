---
name: catalogue-ontology-fill
description: >
  Fill CatalogueOntology v0.1 Deontic Dataset/Distribution/DataService fields from
  vendor product assertions. Use whenever the task is to populate MDS catalogue
  metadata, map CSV headers via csvw:name, or distinguish DistributedDataset
  commercial packages from bare dcat:Dataset.
allowed-tools: lookup_catalogue_term list_catalogue_dataset_fields describe_system_context
---

# Catalogue ontology fill (CatalogueOntology v0.1 Deontic)

You populate **catalogue-side** metadata. You do not publish and you do not invent
rights/contract triples.

## Grounding
- Ontology fixture: `scudo/fixtures/catalogue_ontology_v0_1_deontic.ttl` (canonical
  prefixes — never use truncated `<http://w3.org>` transcript prefixes).
- Call `list_catalogue_dataset_fields` for the Dataset fill map.
- Call `lookup_catalogue_term` for any curie / csvw alias (`PermID`, `AssetClassPermId`, …).

## Critical classes
- `dcat:Dataset` — conceptual dataset
- `cat:DistributedDataset` — commercially licensed vendor package (dual-license risk)
- `dcat:Distribution`, `dcat:DataService`, `cat:DeliveryChannel`, `cat:ProductPackage`
- `cat:DataDictionary` → `cat:FieldGroup` → `cat:Field`
- `cat:DataTaxonomy`, `cat:BusinessConceptElement`

## Rules
- Prefer null over fabricated PermIDs / AssetClass codes.
- Map csvw aliases from source column names when present.
- Licensing / non-display / exchange entitlement language → `requires_human_review`.
