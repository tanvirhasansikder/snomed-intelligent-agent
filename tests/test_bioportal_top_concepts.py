"""BioPortal's SNOMEDCT: the top-level hierarchies are top concepts (no parents), and the root lists no children."""

import pytest

from evaluation.bioportal_cache import CallStore
from evaluation.collect import ROOT_ID, collect, validate_hierarchies
from evaluation.gold import GoldIndex
from evaluation.synthetic import SyntheticOntology
from evaluation.tasks import generate_items


class BioPortalLike:
    """The synthetic ontology as BioPortal serves SNOMEDCT (checked live 2026-11-05)."""

    def __init__(self):
        self.inner = SyntheticOntology(seed=0)
        self.tops = set(self.inner.top_level_names())

    def fetch_children(self, concept_id, max_pages=5, pagesize=100):
        if concept_id == ROOT_ID:
            return {"children": [], "complete": False, "pages_fetched": 1}
        return self.inner.fetch_children(concept_id, max_pages=max_pages, pagesize=pagesize)

    def get_parents(self, concept_id, raise_errors=False):
        if concept_id in self.tops:
            return []
        return self.inner.get_parents(concept_id, raise_errors=raise_errors)

    def get_ancestors(self, concept_id, raise_errors=False, max_pages=20):
        return [a for a in self.inner.get_ancestors(concept_id, raise_errors=raise_errors, max_pages=max_pages)
                if a.get("concept_id") != ROOT_ID]

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_validation_accepts_top_concepts_and_rejects_others():
    live = BioPortalLike()
    tops = sorted(live.tops)
    assert set(validate_hierarchies(live, tops).values()) == {"top_concept"}
    child = live.inner.children[tops[0]][0]
    with pytest.raises(ValueError):
        validate_hierarchies(live, [child])
    with pytest.raises(ValueError):
        validate_hierarchies(live, ["999999999"])


def test_full_structure_still_reports_child_of_root():
    live = SyntheticOntology(seed=0)
    assert set(validate_hierarchies(live, sorted(live.top_level_names())).values()) == {"child_of_root"}


def test_collect_depth_and_t3_targets_on_bioportal_structure(tmp_path):
    live = BioPortalLike()
    config = {"name": "bp", "synthetic": True, "data": {"per_hierarchy": 3, "seed": 5, "min_depth": 2, "max_depth": 6,
                                                        "hierarchies": live.inner.top_level_names()}}
    store = CallStore(tmp_path / "calls.jsonl")
    dataset = collect(config, live, store)
    assert not dataset["failures"] and set(dataset["hierarchy_validation"].values()) == {"top_concept"}
    gold = GoldIndex(store, dataset)
    for concept_id, sample in gold.samples.items():
        shortest, longest = gold.depth(concept_id)
        assert shortest is not None and longest >= shortest >= 1       # depth is measured to the top concept
        assert gold.is_root(sample["hierarchy_id"])
    t3 = [i for i in generate_items(gold, tasks=("T3",)) if i.polarity == "pos"]
    assert t3 and not any(gold.is_root(i.target_id) for i in t3)
