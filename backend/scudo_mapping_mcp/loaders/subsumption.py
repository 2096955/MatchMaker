"""Load-time RDFS subsumption closure (hand-rolled, no owlrl dependency)."""

from __future__ import annotations

from ..models import TaxonomyNode


def materialize_subsumption_closure(
    nodes: list[TaxonomyNode],
    *,
    max_depth: int = 3,
) -> list[TaxonomyNode]:
    """Expand ``superclass_iris`` / ``superproperty_iris`` to ancestor sets.

    Closure is computed at load time so memory, FalkorDB and Neptune backends
    share identical subsumption data without SPARQL property paths.
    """
    if max_depth < 1 or not nodes:
        return nodes

    by_iri = {n.iri: n for n in nodes}
    direct_parents: dict[str, set[str]] = {}
    for n in nodes:
        parents: set[str] = set()
        if n.parent_iri:
            parents.add(n.parent_iri)
        parents.update(n.superclass_iris)
        parents.update(n.superproperty_iris)
        direct_parents[n.iri] = parents

    def ancestors(iri: str) -> set[str]:
        seen: set[str] = set()
        frontier = list(direct_parents.get(iri, set()))
        depth = 0
        while frontier and depth < max_depth:
            depth += 1
            nxt: list[str] = []
            for p in frontier:
                if p in seen or p == iri:
                    continue
                seen.add(p)
                nxt.extend(direct_parents.get(p, set()))
            frontier = nxt
        return seen

    out: list[TaxonomyNode] = []
    for n in nodes:
        anc = sorted(ancestors(n.iri))
        super_cls = [
            a for a in anc if a in by_iri and by_iri[a].node_kind != "property"
        ]
        super_prop = [
            a for a in anc if a in by_iri and by_iri[a].node_kind == "property"
        ]
        if not super_cls and not super_prop:
            super_cls = list(n.superclass_iris)
            super_prop = list(n.superproperty_iris)
        else:
            for d in n.superclass_iris:
                if d not in super_cls:
                    super_cls.append(d)
            for d in n.superproperty_iris:
                if d not in super_prop:
                    super_prop.append(d)
        out.append(
            n.model_copy(
                update={
                    "superclass_iris": super_cls,
                    "superproperty_iris": super_prop,
                }
            )
        )
    return out


def build_child_index(nodes: list[TaxonomyNode]) -> dict[str, list[str]]:
    """Reverse index for 1-hop descendant expansion in retrieval."""
    children: dict[str, set[str]] = {}
    for n in nodes:
        if n.parent_iri:
            children.setdefault(n.parent_iri, set()).add(n.iri)
        for sup in n.superclass_iris:
            children.setdefault(sup, set()).add(n.iri)
        for sup in n.superproperty_iris:
            children.setdefault(sup, set()).add(n.iri)
        for c in n.children_iris:
            children.setdefault(n.iri, set()).add(c)
    return {k: sorted(v) for k, v in children.items()}


def direct_subsumption_neighbors(
    node: TaxonomyNode,
    child_index: dict[str, list[str]],
) -> list[str]:
    """1-hop ancestors + descendants for candidate expansion."""
    neighbors: set[str] = set()
    if node.parent_iri:
        neighbors.add(node.parent_iri)
    neighbors.update(node.superclass_iris)
    neighbors.update(node.superproperty_iris)
    neighbors.update(child_index.get(node.iri, []))
    neighbors.discard(node.iri)
    return sorted(neighbors)
