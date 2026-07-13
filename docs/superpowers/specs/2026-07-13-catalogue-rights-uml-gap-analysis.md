# Catalogue/Rights UML gap analysis — detailed images vs code (2026-07-13, v2)

Two customer-supplied UML images arrived 2026-07-13 with full attribute-level
detail. They supersede the transcript-plus-ODRL grounding the current
"bottom half" model was built on:

| Image | Package | Mermaid transcription |
|-------|---------|----------------------|
| CatalogueOntology-UML ("MDS DataCatalog and Digital Rights") | `CatalogueOntology` | [`docs/architecture/catalogue-ontology-uml.mmd`](../../architecture/catalogue-ontology-uml.mmd) |
| MDSRights-UML | `MarketDataOntology` | [`docs/architecture/mds-rights-uml.mmd`](../../architecture/mds-rights-uml.mmd) |

Where the two images disagree, **MDSRights-UML wins for the rights half** —
it is the refinement (Rule supertype, Obligation taxonomy, Pricelist); the
catalogue image's rights corner is the coarse view of the same structure.
One exception: the catalogue image's Duty—Permission association is retained
as an *optional* link pending ontology-owner confirmation (see D2 below).

## Verification record (v2)

v1 of this doc was independently verified on 2026-07-13 by (a) a Codex
read-only review (thread `019f5b88-8733-78f0-bd73-e31452a1347d`) and (b) a
16-agent adversarial workflow (run `wf_c0e8819d-77f`: 12 per-claim refuters,
3 plan critics, 1 completeness critic; every REFUTED/PARTIAL verdict that
reshaped the plan was then hand-checked against source). Corrections landed
in this v2:

- **G10 was overstated**: `DcatDataset` already carries `themes`/`keywords`
  (`models_dcat.py:24-25`) feeding `alt_labels`/`superclass_iris`.
- **G11 was partly wrong**: `dcat:keyword` already merges into `alt_labels`
  and `dcat:theme` already flows into `superclass_iris` at load
  (`loaders/dcat_loader.py:120-126`). Only `businessConcept` / `assetClass`
  / `superAssetClass` are genuinely new signals.
- **G13 was mischaracterised**: the catalogue image's association is
  Duty→Permission; the code's `HAS_DUTY` is Permission→Duty (ODRL
  direction) — opposite, not equal. The rights image *omits* the link;
  omission does not prove replacement. Also the drawn arrowhead may be
  navigability-only (transcription caveat noted in the .mmd).
- **"Nothing else references the rights kinds" was PARTIAL**: two more
  consumers exist — `agent.py:584-598` (`_system_context_text()` slices
  `ConceptualNodeKind` positionally `[13:]` AND hardcodes the old ODRL
  prose "Policy contains Permission contains Duty; Party is
  assigner/assignee") and `test_zone_context_tool.py:48-49` (hardcodes the
  five kind strings). Still code-only — fixtures/frontend/routes are clean
  (verified) — but both files are now in scope.
- **The claimed Phase D "gate" was a no-op for additions**: both dashboard
  maps fall back to valid enum values (`build_matching_graph.py:342`
  `.get(kind, "entity")`, `:366` `.get(kind, "related")`), so a forgotten
  map entry fails no test. The map-coverage tests must iterate the full
  enums.
- **Store round-trip is a silent-drop, everywhere that matters**:
  `falkordb_store.py:760-830` hardcodes the ConceptualNode column list in
  both SET and RETURN; Neptune's conceptual layer is a documented no-op
  (`neptune_store.py:949-959`); `ingest.py:111-123` and
  `build_matching_graph.py` fixture seeding enumerate explicit kwargs. New
  fields need all of these touched or they vanish without error.

## Implementation status (2026-07-13, Phases A+B executed by Cursor; assessed)

Cursor implemented Phase A and Phase B (with D folded in, as planned). The
work was assessed against this spec by hand-verification plus an independent
Codex review (thread `019f5bfd-4527-7391-9e90-38809f23c150`). Result:
**substantively faithful and green, with 3 pre-commit follow-ups and 1
decision point**.

Verified facts:
- Test gate: `cd backend && PYTHONPATH=. python -m pytest scudo/tests/
  scudo_mapping_mcp/tests/ -q` → **354 passed, 2 failed** — the 2 failures
  are the pre-existing `test_provenance.py` Marketing failures (reproduced
  at HEAD; not a regression).
- Phase A complete: 11 CDM members, citation map updated to the UML-image
  provenance, exact-11 pin test, PROVISIONAL language now `HAS_DUTY`-only.
- Phase B complete per checklist: kinds, subtype validator + cross-pairing
  tests, all edge renames/additions in one pass (no stale
  `PARTY_ROLE`/`HAS_PERMISSION` refs anywhere; fixture holds zero rights
  kinds so nothing breaks), `ConceptualNode` attrs + `ContractTerms`/
  `PartyProfile` sub-models, FalkorDB SET/RETURN extended with JSON-encoded
  sub-models via shared props-codec helpers, Neptune stub comment, agent.py
  de-positional-sliced + prose rewritten to MDSRights, enrich
  classification filter + mixed-graph regression test, dashboard maps +
  subtype-in-tags + full-enum coverage tests, `rights_odrl.py` mapping note.
- **Improvement over plan**: ingest + fixture seeding now share one
  `conceptual_node_from_fixture_raw()` helper instead of two kwarg loops —
  a third silent-drop path is structurally closed.
- SET-string params ⇄ `conceptual_node_props` keys verified in sync today
  (empirically diffed — no missing keys either direction).

**Decision point (deviation from D1 wording)** — `subtype` is REQUIRED for
DOCUMENT/OBLIGATION nodes, not optional: a generic Document with unknown
subtype raises `ValidationError` (hand-verified), and a test pins that.
The plan typed it `Optional[...]`. The strict form matches the UML's
concrete subclasses but makes an ETL'd document of unknown flavour
unrepresentable. Recommendation: relax to allow `None` (= "unclassified")
while still rejecting wrong-kind pairings — awaiting user call.
**RESOLVED (user, 2026-07-13): keep REQUIRED as implemented.** The strict
form stands; `test_rights_contract_model.py` pins it (missing-subtype
DOCUMENT raises). If an ETL source ever surfaces a genuinely
unclassifiable document, that becomes a new decision, not a silent relax.

**Phase B follow-ups (before the single Phase-B commit):**
1. ~~Cypher clause sync-guard test~~ **DONE 2026-07-13** (post-review):
   `test_conceptual_store_roundtrip.py` now pins every
   `conceptual_node_props()` key to a `$param` in the live
   `upsert_conceptual_node` SET source and every `ConceptualNode` field to
   an `n.<field>` RETURN column in `get_conceptual_graph`
   (`inspect.getsource`, no container needed). A second independent review
   (Claude, replacing the dead Cursor reviewer session) APPROVEd Phase B
   and confirmed the live clause lists were already complete (18/18).
2. ~~Nondeterministic rights-kind ordering~~ **DONE 2026-07-13**:
   `agent.py` now iterates `ConceptualNodeKind` filtered by membership;
   verified stable across `PYTHONHASHSEED` 0/1/42.
3. `routes/mapping.py` commingles the rights-half enrich filter with the
   unrelated in-flight SSE heartbeat refactor. Stage by hunk (or land the
   SSE work first) so the Phase-B commit stays single-purpose. The three
   new test files + the two `.mmd` transcriptions + this spec are untracked
   and must be added deliberately. (STILL OPEN — commit-time concern.)

**Flagged into the SSE work stream (pre-existing, NOT Phase B):** if the
bounded agent-run queue is full when the worker unwinds, `_safe_put(q,
None)` drops the completion sentinel — a slow-but-alive consumer then
drains the queue and heartbeats forever. Fix belongs with the SSE/CloudFront
timeout stream.

**Phase C executed (2026-07-13, Claude):** TDD'd on top of the uncommitted
A+B tree; focused suite 370 passed / 2 failed (the known `test_provenance.py`
Marketing pair), standalone smoke 117/117.

- `models.py`: `ConceptualNode` gained `sequence_number` (Field+FieldGroup),
  `notation`, and `group_type` (UML FieldGroup `type` → `group_type`, mapping
  documented in a comment citing the .mmd). G9 documented as UNDECIDABLE with
  both readings + `TODO(g9)` (vendor_field_name may be the vendor-side
  notation equivalent or a distinct alias; a FIELD node may carry both).
  `conceptual_node_from_fixture_raw` propagates all three.
- Store propagation: `conceptual_node_props` / `conceptual_node_from_props`,
  live SET clause in `upsert_conceptual_node`, live RETURN list + row unpack
  in `get_conceptual_graph` (falkordb_store.py) all extended; the A+B
  clause-coverage tests were made to fail first, then pass. Neptune stub
  comment notes Phase C fields are also not persisted. FakeStore needed no
  change (whole-model). Round-trip node in
  `test_conceptual_store_roundtrip.py` now fully populated incl. Phase C
  fields, plus a fixture-raw propagation test.
- `models_dcat.py`: `DcatDistribution` +`media_type`/`conforms_to`/
  `distribution_type`; `DcatDataService` +`service_type` (UML `type`, same
  rename discipline)/`deployment_type`/`api_type`/`dataset_iri`;
  `DcatDataset` +`update_period`/`business_concept`/`asset_class`/
  `super_asset_class` + the coverage block `geographic_coverage`/
  `temporal_coverage`/`industry_coverage`/`content_type_coverage` (plain
  optional strings — Phase E boundary documented in the module docstring
  and enforced by a no-leak test covering all eight). G19 shapes
  `DcatProductPackage`/`DcatDeliveryChannel`/`DcatDataDictionary` added as
  pydantic models only — verified the loader's `_is_taxonomy_entity` does
  NOT recognise cat:ProductPackage/DeliveryChannel/DataDictionary subjects
  today; comment says to wire recognition when a customer export carries
  them.
- Lockstep: new `extract_dcat_dataset` / `extract_dcat_distribution` /
  `extract_dcat_data_service` in `dcat_loader.py` (cat: predicates via the
  shared `CAT_ONTOLOGY_NS` constant from `csvw_aliases.py` — G20: no second
  hardcoding, no new vendor-facing aliases introduced).
  `test_dcat_phase_c_lockstep.py` feeds one in-memory rdflib Graph to both
  paths: Distribution/DataService TaxonomyNodes are asserted IDENTICAL;
  Dataset is full `model_dump()` equality after normalising ONE documented
  pre-existing delta — `parent_iri` (loader: skos:broader→None; projector:
  themes[0]), whose endpoint values are pinned explicitly first.
- `enrichment.py`: validation loop extracted to `_validated_fields`;
  allowed-keys contract now accepts `nullable` (bool) and `sequence_number`
  (int; bool rejected), coercing off-type values to None. This
  coerce-to-None is the LLM trust boundary (sanitising model-proposed
  junk); the MODEL itself is stricter — see next bullet. Live
  `_EXTRACT_SYSTEM_PROMPT` text deliberately UNCHANGED (acceptance-side
  only) and a test pins that the prompt does not mention the new keys.
  Accepted values land on the FIELD ConceptualNode.
- Post-review hardening (REWORK round, 2026-07-13): `ConceptualNode` gained
  a `field_validator("sequence_number", mode="before")` — accepts None and
  true ints only (bool excluded explicitly, numeric strings rejected),
  closing the drift where pydantic's lax `Optional[int]` coerced `True`→1 /
  `"3"`→3 on the fixture-raw and Falkor props-decode paths while enrichment
  strictly rejected the same values. Corrupt fixture/store data now fails
  loudly at ingest; tests cover the model, the fixture-raw helper, and the
  props codec.

**Phase E executed (2026-07-13, Claude; independent review APPROVE after
one REWORK round):** steps 1–4 + 6 and step 5's calibrate tooling; the
operational shadow window, exit-criteria evaluation, and any band decision
remain with the humans. Gates at close: full suite 420 passed / 2 failed
(the pre-existing `test_provenance.py` Marketing pair), smoke 117/117.

- Step 1: `TaxonomyNode` +`business_concept`/`asset_class`/
  `super_asset_class`, loaded unconditionally by `_extract_node` (never
  concatenated into `definition`); composed into BM25 text only behind
  `SCUDO_TAXONOMY_UML_TEXT` (default off, call-time env read — the frozen
  `Settings` snapshot is introspection-only after the REWORK round).
  Projector path (`project_dcat_dataset`) surfaces the same three fields;
  lockstep test extended with an explicit population pin.
- Step 2: `maybe_log_taxonomy_text_shadow` wired into the DEFAULT legacy
  BM25 sidecars (`memory_store.py`, `falkordb_store.py`);
  `make_opus_dense_scorer` passes `taxonomy_candidate_desc(c.node)`
  (flag-gated, off → `""` as before); shadow diff emits counted EMF metric
  `TaxonomyShadowDiff` (778b47a pattern, never load-bearing, zero-diff
  emits nothing).
- Step 3: signals ride the alt-label/BM25 channel only;
  `taxonomy_dense_text` pinned to label+definition (floor neutrality vs the
  0.80/0.70 bands — pin test asserts `Candidate.similarity` cannot move);
  IRI-valued businessConcept resolves to prefLabel; alt+signal block capped
  at 2000 chars symmetric with the definition cap.
- Step 4: node `asset_class` plumbs into
  `run_validations(node_data_class=…)` behind its OWN flag
  `SCUDO_ASSET_CLASS_VALIDATION` (default off, deliberately decoupled from
  the text flag — REWORK blocker: the first cut plumbed unconditionally,
  which would have flipped mappings to required-FAIL on merely loading an
  enriched catalogue with all flags off). Flag-off inertness pinned by
  test.
- Step 5 (tooling): `calibrate_confidence_floor.py` gained
  `LabelledPrecedent`/`rescore_cases` real re-scoring (4dp Jaro-Winkler
  parity), functional `--with-definitions`, `--dense-backend` hard-limited
  to the implemented scorer; report-only, bands untouched.
- Step 6: I5 comment names `classify_business_concept` and the
  never-write-back rule; test pins the enrich route writes only
  ConceptualNodes. Characterisation test documents that a subject typed
  only `cat:MarketingDataset` is DROPPED by `_is_taxonomy_entity` today
  (ontology owner must confirm the customer export co-types
  `dcat:Dataset`, else Phase E enrichment is moot for those rows).
  **RESOLVED (user, 2026-07-13): keep with dcat** — the loader keeps
  requiring `dcat:Dataset` (co-)typing; `_is_taxonomy_entity` is NOT
  widened to accept bare `cat:MarketingDataset`. The co-typing
  requirement is the customer-export contract; the characterisation
  test in `test_phase_e_provenance_guard.py` stays as the pin.
- Store persistence (reviewed additive deviation): the three signals
  persist through FalkorDB SET/read sites and Neptune SPARQL templates —
  including `SPARQL_LIST_ALL_TAXONOMY` (REWORK blocker: the list path
  initially dropped them) — with a clause-coverage guard over every
  full-node SPARQL SELECT so a future field cannot miss a read query.
  Unused `SPARQL_LIST_TAXONOMY_NODES` excluded by design (zero call
  sites).

Minor notes (recorded, no action needed now): the `HAS_DUTY` citation guard
validates a test-local map (weaker than the CDM full-enum sweep, but the
enum has exactly one provisional member today); `DocumentSubtype`/
`ObligationSubtype` Literal aliases are exported but unused
(`ConceptualSubtype` is the one wired in); subset pytest invocations that
skip the conftest env setup hit redis-connection errors — pre-existing
test-isolation quirk, reproduced at HEAD.

## Decisions (user, 2026-07-13)

- **D1 — subtypes as attribute**: Document/Obligation subtypes are a closed
  per-kind `subtype` attribute on `ConceptualNode`, not 6 more enum kinds.
  Must be a true `Literal` union plus a `model_validator` pinning allowed
  subtype values per kind (a shared flat Literal would accept
  `kind=DOCUMENT, subtype="direct_access"`).
- **D2 — Duty—Permission link stays, as optional**: rather than dropping it
  or blocking on the ontology owner, RETAIN the existing `HAS_DUTY`
  (Permission→Duty) as the optional contested link. ODRL 2.2 gives exactly
  this direction defensible pre-condition semantics (`odrl:duty`, domain
  Permission, range Duty); no ODRL property runs Duty→Permission, and the
  image's arrowhead may be navigability-only. Do NOT add a reversed
  `CONSTRAINS` kind (name collides with ODRL Constraint — `rights_odrl.py:96`
  fails closed on `constraint*` keys). Optionality is carried as provenance
  with teeth: a PROVISIONAL comment on the member citing the two images +
  G13, plus a citation-guard test in the style of
  `_CONTENT_DELIVERY_MODEL_SOURCES` so the pending question resurfaces
  instead of fossilising. Ask the ontology owner two things: (a) does the
  link survive the Rule refactor, (b) if yes, its direction and role name.
- **D3 — matching signals are IN scope**: the Dataset attributes that are
  matching signals (`businessConcept`, `assetClass`, `superAssetClass`) are
  promoted into the plan as Phase E with a measured rollout (JPMC scope;
  added functionality beats token/cost thrift), under the discipline below.

## What the images newly establish

1. **`ContentDeliveryModel` is now fully sourced — 11 literals** (identical
   in both images): `distributionService`, `redistributionService`,
   `useService`, `displayService`, `directDisplayService`,
   `nonDisplayService`, `fullNonDisplayService`, `automatedTradingService`,
   `derivedDataService`, `internalDistributionService`,
   `directAccessService`. This is the citable source the
   `TODO(content-delivery-model)` in `models.py` has been waiting for.
2. **`Rule` supertype** with `timeInterval: String [1]` and
   `deadline: DateTime [1]`; `Duty` and `Permission` both specialise it.
3. **Rules bind parties directly**: `Rule --ruleObject--> Party [1]` and
   `Rule --ruleSubject--> Party [1]`. Party attachment is at *rule* level,
   not policy level. (Obligation ⊂ Duty ⊂ Rule, so obligation nodes carry
   these edges too.)
4. **`Obligation` specialises `Duty`**, and `DirectAccess` /
   `InternalDistribution` specialise `Obligation`.
5. **`Policy.cdm : ContentDeliveryModel [1]`** (both images agree).
6. **Document taxonomy**: `OrderForm`, `Schedule`, `MasterAgreement` (both
   images) + `Pricelist` (rights image only); `Contract --contractDocuments-->
   Document [1..*]`.
7. **Full attribute lists** for Party (6), Contract (11), Dataset (~21
   catalogue-side + `updatePeriod: Duration` rights-side only), Field
   (+`sequenceNumber`), FieldGroup (+`notation`, `type`, `sequenceNumber`),
   DataService (`type`, `deploymentType`, `apiType`, `accessURL`,
   `dataset_identifier`), Distribution (`mediaType`, `conformsTo`,
   `distribution_type`, `dataset_identifier`), DeliveryChannel (`title`,
   `distribution_type`), ProductPackage (`longName`, `ProductID`,
   `description`, `dataset_identifier: ProdDataSetMap`), DataDictionary
   (`dataset_identifier`, `title`), DataTaxonomy (`title`, `description`).
8. **InternalParty / ExternalParty** subtypes (both images).
9. **Named attribute types**: `SupplyChainStatus`, `OrganizationType`
   (Party); `ContractStatus`, `LegalBasis`, `LicensingModel`, `RenewalType`
   (Contract); `Duration`/`Date` fields; `ProdDataSetMap` (ProductPackage).
   None exist anywhere in the codebase.
10. **Cross-half anchor edges**: `Dataset --> Party` (rights image; the only
    bridge from catalogue to rights halves), `ProductPackage --> "1..*"
    Dataset`, `Distribution --> DataDictionary`, `Dataset --> "0..1"
    DataTaxonomy` (attached to *Dataset*, where the code's `CLASSIFIED_AS`
    routes via BusinessConceptElement — a transcript-vs-image discrepancy to
    flag to the ontology owner, since BCE appears in neither image).

## Gap table — images vs `backend/scudo_mapping_mcp/` (v2)

| # | Image detail | Code today | Gap class |
|---|--------------|------------|-----------|
| G1 | 11 CDM literals | `ContentDeliveryModel` has 3 (deliberately incomplete, guard test) | **Complete now** — source found |
| G2 | `Rule` supertype (timeInterval, deadline) | No Rule anywhere | Structural add (attrs on duty/permission nodes; no separate kind) |
| G3 | `ruleObject`/`ruleSubject` → Party | `PARTY_ROLE` edge is Party→Policy (ODRL guess) | **Correction** — party binds at rule level |
| G4 | `policyDuties`: Policy→Duty direct | No Policy→Duty edge (`HAS_DUTY` is Permission→Duty) | Structural add (`POLICY_HAS_DUTY`); `HAS_DUTY` retained as optional per D2 |
| G5 | Obligation ⊂ Duty; DirectAccess / InternalDistribution ⊂ Obligation | Absent | Structural add |
| G6 | Document + 4 subtypes; contractDocuments 1..* | Absent | Structural add |
| G7 | `Policy.cdm` attribute | Enum attached to nothing | Attribute wiring |
| G8 | Party (6) / Contract (11) / Rule (2) attribute lists | `ConceptualNode` has no rights-side attributes | Attribute enrichment (sub-models) |
| G9 | Field `notation`+`sequenceNumber`; FieldGroup `notation`/`type`/`sequenceNumber` | 4 field attrs + 2 fieldgroup attrs; `vendor_field_name` vs `notation` relationship unstated | Attribute enrichment |
| G10 | DataService/Distribution full attrs (`mediaType`, `conformsTo`, `type`, `deploymentType`, `apiType`, `distribution_type`, `dataset_identifier`×2) | `models_dcat.py` has title/description/access_url/endpoint_url (+ Dataset themes/keywords, which DO exist) | Attribute enrichment (loader side) |
| G11 | Dataset matcher-relevant attrs | `keyword`→alt_labels and `theme`→superclass_iris ALREADY flow at load; `businessConcept`/`assetClass`/`superAssetClass` absent | **Matching-signal work = Phase E** (per D3) |
| G12 | InternalParty/ExternalParty | Single `PARTY` kind | `party_scope` attribute |
| G13 | Duty—Permission association (catalogue img); absent in rights img | `HAS_DUTY` Permission→Duty exists | **Keep as optional** (D2), provenance-guarded |
| G14 | `Dataset --> Party` (rights img — the catalogue↔rights bridge) | No edge kind | Structural add (`GOVERNED_FOR` / `DATASET_PARTY`) |
| G15 | `ProductPackage --> 1..* Dataset` | No edge kind; fixture's product_package is an orphan | Structural add (reuse `CONTAINS`) |
| G16 | `Distribution --> DataDictionary`; `DataDictionary 1→1 FieldGroup` | No Distribution→DataDictionary kind; dictionary linked only downward via generic `contains` | Structural add (reuse `CONTAINS` or `DESCRIBED_BY`) |
| G17 | `Dataset --> 0..1 DataTaxonomy` attaches at Dataset | `CLASSIFIED_AS` routes via BusinessConceptElement (transcript-era) | Discrepancy — flag to ontology owner; keep BCE routing meanwhile |
| G18 | 6 named enum types + Duration/Date + ProdDataSetMap | Nothing | Typing decision: strings-with-documented-TODO now, enums when literals are sourced (same citation-guard discipline as CDM) |
| G19 | ProductPackage/DeliveryChannel/DataDictionary/DataTaxonomy attrs; `Dataset.updatePeriod` | `ConceptualNode` has no `description` field at all; no Dcat models for these classes | Attribute enrichment |
| G20 | `longName` / `featuresAndBenefitsDescription` | ALREADY mapped in `csvw_aliases.py:15-24` — flattened onto vendor product name/description with no class distinction | Consistency constraint on Phase C naming, not net-new |

Non-gaps (deliberate, keep): `DELIVERY_PRODUCT`, `DISTRIBUTED_DATASET`,
`MARKETING_DATASET`, `BUSINESS_CONCEPT_ELEMENT`, `BUSINESS_DATA_ELEMENT`
come from the earlier CatalogueOntology *transcript* and don't appear in
these UML views — class diagrams of two packages, not an exhaustive union.
Do not remove transcript-grounded kinds. (But note G17: BCE's edge role now
conflicts with the image and needs owner adjudication.)

## Update plan v2 (phased; B and D merged — see sequencing note)

### Phase A — enum completion + provenance refresh (small, first)
- Add the 8 missing `ContentDeliveryModel` members, citing
  "CatalogueOntology-UML / MDSRights-UML customer images, received
  2026-07-13, transcribed in docs/architecture/*.mmd" in
  `_CONTENT_DELIVERY_MODEL_SOURCES`.
- Update `test_content_delivery_model_has_only_confirmed_values` to the
  full 11-value set; drop "deliberately incomplete" language here and in
  `models.py`.
- Refresh the PROVISIONAL comments in `models.py`: grounding is now the
  MDSRights-UML image. Keep PROVISIONAL only on `HAS_DUTY` (per D2).

### Phase B — rights structure (single commit with all consumers)
**Sequencing (hard requirement)**: enum changes, dashboard maps, and every
test that references members BY ATTRIBUTE must land in ONE commit —
`test_dashboard_enum_vocabulary.py:105,108` references
`ConceptualEdgeKind.PARTY_ROLE`/`.HAS_DUTY` directly, so a staged removal
breaks test collection for the whole module. "Deprecate-then-remove" is
replaced by immediate rename/removal: this is the one sanctioned breaking
pass over provisional values (no data exists — verified).

- **Node kinds**: add `OBLIGATION`, `DOCUMENT`. Subtypes per D1:
  `subtype: Optional[Literal["order_form","schedule","pricelist",
  "master_agreement","direct_access","internal_distribution"]]` with a
  `model_validator` enforcing per-kind legality ({DOCUMENT: 4 doc subtypes,
  OBLIGATION: 2, all other kinds: None}) + a test asserting the invalid
  cross-pairing raises. `Rule` stays abstract — its attrs live on
  duty/permission/obligation nodes.
- **Edge kinds**:
  - Add `POLICY_HAS_DUTY` (Policy→Duty, `policyDuties`), `RULE_OBJECT` and
    `RULE_SUBJECT` (Duty/Permission/Obligation→Party), `CONTRACT_DOCUMENTS`
    (Contract→Document), and a Dataset→Party kind (G14, name
    `DATASET_PARTY` unless the owner supplies the role name).
  - Rename `HAS_PERMISSION`→`POLICY_HAS_PERMISSION` in the same commit
    (symmetry with `POLICY_HAS_DUTY`; cheap now, expensive after data).
  - Remove `PARTY_ROLE`. RETAIN `HAS_DUTY` as the optional contested link
    (D2) with a PROVISIONAL citation comment + a sources-map guard test
    mirroring the CDM one.
  - G15/G16: reuse generic `CONTAINS` for ProductPackage→Dataset and
    DataDictionary→FieldGroup already does; add Distribution→DataDictionary
    via `CONTAINS` too (no new vocabulary until the owner names these).
- **Attributes on `ConceptualNode`**: `subtype`, `cdm:
  Optional[ContentDeliveryModel]` (Policy), `time_interval` + `deadline`
  (rules), `party_scope: Optional[Literal["internal","external"]]`,
  `description: Optional[str]` (G19), plus sub-models `contract_terms:
  Optional[ContractTerms]` (11 attrs) and `party_profile:
  Optional[PartyProfile]` (6 attrs). G18: type the enum-ish members as
  documented strings for now (`status`, `legal_basis`, `licensing_model`,
  `renewal_type`, `supply_chain_status`, `organization_type`) with a
  TODO-to-enum citing that no literal lists exist in the images; promote to
  guard-tested enums when the customer supplies values.
- **Store layer (blocker fix — in scope)**:
  - `falkordb_store.py`: extend `upsert_conceptual_node` SET list and
    `get_conceptual_graph` RETURN list; JSON-encode `contract_terms` /
    `party_profile` as string properties (Cypher can't store nested maps on
    this path). Add a store-contract round-trip test parametrised over
    FakeStore + FalkorDB asserting field-for-field equality of a
    fully-populated rights node.
  - `neptune_store.py`: remains a documented no-op — extend the stub
    comment to note the new fields are also not persisted.
  - `ingest.py:111-123` + `build_matching_graph.py` fixture seeding: extend
    both explicit kwarg loops.
- **Hidden consumers (in scope)**:
  - `agent.py:_system_context_text()`: replace the positional
    `all_kinds[:13]`/`[13:]` slice with an explicit frozenset of
    rights-half kinds; rewrite the hardcoded prose to the MDSRights
    structure (Rule supertype, policyDuties/policyPermissions,
    ruleObject/ruleSubject). `test_zone_context_tool.py` literal list gains
    `obligation`, `document`.
  - `routes/mapping.py` `/mapping/enrich`: filter the candidate list passed
    to `classify_business_concept` to classification-eligible kinds
    (exclude the rights half) + regression test with a mixed-kind graph —
    otherwise a CONTRACT/PARTY node can be picked as a "business concept".
- **Dashboard maps + gate hardening (formerly Phase D, folded in)**:
  - `_CONCEPTUAL_NODE_TYPE`: `obligation`→`step`, `document`→`document`.
  - `_CONCEPTUAL_EDGE_TYPE`: `policy_has_duty`→`contains`,
    `policy_has_permission`→`contains`, `rule_object`/`rule_subject`→
    `related`, `contract_documents`→`contains`, `dataset_party`→`related`.
    `has_duty` keeps `contains` (no cycle: no reversed edge exists per D2).
  - Surface `subtype` in the dashboard fold-in (`tags` gains the subtype
    value) so D1's carrier is visible.
  - **Harden the gate**: rewrite the map-coverage tests to iterate the FULL
    enums (`for kind in ConceptualNodeKind: assert kind.value in
    _CONCEPTUAL_NODE_TYPE`, same for edges) — today the in-enum fallbacks
    make a forgotten entry invisible.
- `rights_odrl.py`: behaviourally untouched (verified: imports only
  ScopeResult/VendorProductRef); add the mapping note (UML Rule ≙
  odrl:Rule, UML Obligation ≙ odrl:Duty, ruleObject/ruleSubject ≙
  target/assignee).

### Phase C — catalogue-half attribute enrichment (NEXT — A/B done, see status)
- `ConceptualNode`: `sequence_number` (Field + FieldGroup), `notation` +
  `group_type` (FieldGroup; `type` collides with common vocabulary — map
  UML `type` explicitly), document whether `vendor_field_name` IS the
  Field `notation` equivalent or a distinct vendor-side alias (G9).
- `models_dcat.py`: widen `DcatDistribution` (`media_type`, `conforms_to`,
  `distribution_type`, `dataset_iri` exists), `DcatDataService`
  (`service_type`, `deployment_type`, `api_type`, `dataset_iri`), and
  `DcatDataset` (`update_period`, coverage/assetClass block as structured
  fields — see Phase E for the matching-signal subset). Add
  `DcatProductPackage`, `DcatDeliveryChannel`, `DcatDataDictionary` shapes
  (G19) as needed by the loader.
- **Lockstep rule**: `dcat_loader.py:_extract_node` builds `TaxonomyNode`
  directly; `models_dcat.py` projectors are a parallel path used by
  `enrichment_dcat_projection.py`. Extend BOTH in the same change, with a
  test asserting the two paths produce identical nodes for the same input.
- **G20 consistency**: `csvw_aliases.py` already canonicalises
  `longName`→`name` and `featuresAndBenefitsDescription`→`description` on
  the ingest path. Phase C naming must not diverge from those mappings.
- `enrichment.py` extractor asymmetry (noted, not blocking): the prompt
  contract never emits `nullable`/`sequence_number` though fixtures carry
  them — extend the allowed-keys contract when the fields land.
- Deliberately deferred (ontology-owner call, not in Phase C's list): the
  remaining UML Dataset attrs `startDate`/`endDate`/`temporal`/
  `publicationSchedule`/`spatial`/`extent` — several already have dcterms/
  dcat homes in the transcript fixture and need an owner decision on which
  vocabulary wins before modelling.

### Phase E — Dataset matching signals (promoted per D3, measured rollout)
Verified seam facts this phase is built on:
- `keyword` and `theme` ALREADY flow (alt_labels / superclass_iris) — the
  genuinely new signals are `businessConcept`, `assetClass`,
  `superAssetClass`.
- The flag story is subtler than v1 claimed: on the DEFAULT legacy store
  path, BM25 participates in RRF fusion (reorders candidates → changes
  which candidate's dense score the gate sees); on the multi-path route,
  `opus_dense.py:293` hardcodes `candidate_desc=""` so definitions never
  reach the dense prompt at all; and the shadow logger is wired ONLY into
  the multi-path route (default off), so shadow mode currently produces
  zero signal in default environments.

Steps, in order (each gated on the previous):
1. **Structured fields, not text-merging**: read the new predicates
   unconditionally at load into NEW `TaxonomyNode` fields
   (`business_concept`, `asset_class`, `super_asset_class`) — never
   pre-concatenate into `definition` (irreversible per loaded graph;
   destroys flag separation and shadow attribution). Compose them into
   BM25/dense text at flag time behind a sub-flag
   (`SCUDO_TAXONOMY_UML_TEXT`) so their marginal impact is separately
   measurable and reversible. Flag-off inertness tests mirror
   `test_taxonomy_text_shadow.py:53-62`.
2. **Fix the measurement gaps first**: wire
   `maybe_log_taxonomy_text_shadow` into the legacy store BM25 sidecar
   (`memory_store.py` + `falkordb_store.py` call sites) with a test; fix
   `make_opus_dense_scorer` to pass `taxonomy_candidate_desc(c.node)`
   (flag-gated, behaviour unchanged while off) with a test pinning that
   enriched text reaches the Opus prompt; make the shadow diff a counted
   EMF metric, not a DEBUG log.
3. **Floor-neutral channel by default**: route the enum-ish attributes
   (assetClass, superAssetClass, businessConcept label) into
   `alt_labels`/BM25-only composition — `taxonomy_dense_text` reads only
   label+definition, so alt_labels changes affect nomination recall but can
   NEVER move `Candidate.similarity` on either dense backend. This is the
   structural answer to Jaro-Winkler length-sensitivity drift against the
   calibrated 0.80/0.70 bands. Only genuine prose goes anywhere near
   `definition`, and only after step 5. Resolve prefLabels for theme /
   businessConcept references — never raw IRIs (tokeniser junk); cap
   alt_labels symmetrically with the 2000-char definition cap (BM25 avgdl
   is global — uncapped enrichment shifts scores of unenriched docs too).
4. **Deterministic assetClass seam (highest value, zero floor risk)**:
   plumb the loaded `asset_class` into `run_validations`' existing
   `node_data_class` parameter (`validations.py` already reads vendor-side
   `assetClass` from `ref.raw`) so vendor-vs-node asset-class disagreement
   becomes an explainable deterministic validation failure rather than
   probabilistic soup. OPT-IN behind its own flag
   `SCUDO_ASSET_CLASS_VALIDATION` (default off, call-time env read) —
   measured-rollout discipline: loading a UML-enriched catalogue must not
   change any matching outcome until the operator flips this lever. Kept
   SEPARATE from `SCUDO_TAXONOMY_UML_TEXT` because the text channel and the
   validation seam are independent rollout levers.
5. **Shadow window then calibration decision**: shadow on in dev/UAT with
   the enriched graph; quantitative exit criteria (nomination added/removed
   rate below an agreed bound AND PASS-precision at 0.80 on labelled
   precedents no worse than baseline). Make
   `calibrate_confidence_floor.py` real (labelled precedent edges,
   actual re-scoring with/without enrichment) — its `--with-definitions`
   flag is currently parsed and ignored. If the floor must move, that is a
   5-zone contract change (FE `DEFAULT_BANDS` sync, Nigel-approved bands) —
   separate sign-off.
6. **Provenance guard (I5 boundary)**: only customer-curated catalogue text
   may feed matching text; SCUDO-inferred conceptual metadata
   (`classify_business_concept` output) must NEVER be written back into
   `TaxonomyNode` text fields — that would close an LLM feedback loop into
   the gate. Amend the I5 comment in `models.py` to state this boundary
   explicitly. Also verify the customer's UML→RDF export types datasets as
   `dcat:Dataset` (a `cat:MarketingDataset`-typed subject would be dropped
   by `_is_taxonomy_entity` today, making the enrichment moot).

### Downstream propagation notes
- Fixture/dashboard: no regen needed until rights nodes enter
  `conceptual_layer.json` (verified: zero rights kinds today). When they
  do: `python -m backend.scudo.build_matching_graph`, then
  `infra/build_dashboard_dist.sh`.
- Dashboard z.enum legality of every proposed mapping was verified against
  `test_dashboard_enum_vocabulary.py`'s 21-node/35-edge vocabularies.

### Open questions (ontology owner, not blocking)
1. G13/D2: does the Duty—Permission link survive the Rule refactor, and if
   so what are its direction and role name? (We keep ODRL-direction
   `HAS_DUTY` meanwhile.)
2. G17: is `DataTaxonomy` attached at Dataset (image) or via
   BusinessConceptElement (transcript)? BCE appears in neither image.
3. G18: literal values for `SupplyChainStatus`, `OrganizationType`,
   `ContractStatus`, `LegalBasis`, `LicensingModel`, `RenewalType`, and the
   shape of `ProdDataSetMap` — needed to promote strings to guard-tested
   enums.
4. G14: the role name for the Dataset→Party association.
