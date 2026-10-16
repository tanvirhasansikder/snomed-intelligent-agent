"""
Reproducible benchmark framework for SNOMED CT hierarchy questions.

Status: framework only. No real benchmark has been built or run in this
project yet, and no results exist. See docs/EVALUATION_PROTOCOL.md.

Pipeline
--------
1. build_gold_snapshot(client, concept_ids)  -> GoldSnapshot
   Fetches, for each sampled concept: label, direct parents, direct children
   (complete or flagged truncated) and the full ancestor set, plus the
   BioPortal release version. Failures are recorded, never silently dropped.
2. GoldSnapshot.save(path) / GoldSnapshot.load(path)
   Freezes the gold data with provenance so later runs do not depend on live
   BioPortal. Snapshots contain SNOMED CT content: keep them out of git and
   check the licence before sharing (default folder data/gold/ is ignored).
3. generate_items(snapshot, seed)            -> list[BenchmarkItem]
   T1 direct parents, T2 direct children (only if complete), T3 "is X a kind
   of Y" with positive and random negative pairs. Deterministic for a seed.
4. answer_from_graph(items, graph)           -> predictions
   Reference system "R0": answers from a local graph (e.g. the Neo4j subgraph
   produced by the crawler). Use it to measure the effect of retrieval
   completeness without any LLM.
5. evaluate(items, predictions)              -> metrics per task
"""

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from crawler.bioportal_client import BioPortalError
from evaluation.metrics import accuracy, macro_average, set_prf
from explanation.explainer import ReferenceHierarchy

SCHEMA_VERSION = "1.0"
TASKS = ("T1_direct_parents", "T2_direct_children", "T3_is_a")
DEFAULT_GOLD_DIR = Path(__file__).resolve().parent.parent / "data" / "gold"
MAX_LIVE_CONCEPTS = 50  # guard against accidental large crawls


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================
# Gold snapshot
# ============================================================

@dataclass
class GoldConcept:
    concept_id: str
    label: str
    parents: list
    children: list = None            # None if not retrieved
    children_complete: bool = False
    ancestors: list = None           # None if not retrieved


@dataclass
class GoldSnapshot:
    source: str
    ontology: str
    version: str = None
    released: str = None
    retrieved_at: str = None
    synthetic: bool = False
    notes: str = ""
    concepts: dict = field(default_factory=dict)    # concept_id -> GoldConcept
    labels: dict = field(default_factory=dict)      # every ID seen -> label
    failures: dict = field(default_factory=dict)    # concept_id -> reason
    schema_version: str = SCHEMA_VERSION

    def to_dict(self):
        data = asdict(self)
        data["concepts"] = {cid: asdict(c) for cid, c in self.concepts.items()}
        return data

    @classmethod
    def from_dict(cls, data):
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported gold snapshot schema version: {data.get('schema_version')}")
        concepts = {cid: GoldConcept(**c) for cid, c in data.get("concepts", {}).items()}
        fields = {k: v for k, v in data.items() if k != "concepts"}
        return cls(concepts=concepts, **fields)

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def reference_hierarchy(self):
        """Trusted hierarchy for the claim verifier (explanation.explainer)."""
        edges = set()
        ancestor_sets = {}
        for c in self.concepts.values():
            edges.update((c.concept_id, p) for p in c.parents)
            if c.children_complete and c.children is not None:
                edges.update((child, c.concept_id) for child in c.children)
            if c.ancestors is not None:
                ancestor_sets[c.concept_id] = c.ancestors
        return ReferenceHierarchy(edges, nodes=self.labels.keys(), ancestor_sets=ancestor_sets)


def build_gold_snapshot(client, concept_ids, max_child_pages=10, notes="", allow_large=False):
    """
    Fetch gold data for ``concept_ids`` with strict error handling.

    A concept with any failed request is recorded in ``failures`` and left out
    of ``concepts`` (it is never stored with partial gold answers).
    """

    concept_ids = list(dict.fromkeys(str(c).strip() for c in concept_ids if str(c).strip()))
    if len(concept_ids) > MAX_LIVE_CONCEPTS and not allow_large:
        raise ValueError(f"Refusing to fetch more than {MAX_LIVE_CONCEPTS} concepts without allow_large=True")

    version = client.get_ontology_version() if hasattr(client, "get_ontology_version") else None
    snapshot = GoldSnapshot(
        source="BioPortal REST API",
        ontology="SNOMEDCT",
        version=(version or {}).get("version"),
        released=(version or {}).get("released"),
        retrieved_at=_utc_now(),
        notes=notes,
    )

    for concept_id in concept_ids:
        try:
            concept = client.get_concept(concept_id, raise_errors=True)
            if not concept:
                snapshot.failures[concept_id] = "not found"
                continue
            label = concept.get("prefLabel") or concept_id
            parents = client.get_parents(concept_id, raise_errors=True)
            children = client.fetch_children(concept_id, max_pages=max_child_pages)
            ancestors = client.get_ancestors(concept_id, raise_errors=True)
        except BioPortalError as exc:
            snapshot.failures[concept_id] = str(exc)
            continue

        snapshot.labels[concept_id] = label
        for record in list(parents) + list(children["children"]) + list(ancestors):
            snapshot.labels.setdefault(record["concept_id"], record.get("name") or record["concept_id"])

        snapshot.concepts[concept_id] = GoldConcept(
            concept_id=concept_id,
            label=label,
            parents=sorted(p["concept_id"] for p in parents),
            children=sorted(c["concept_id"] for c in children["children"]),
            children_complete=bool(children["complete"]),
            ancestors=sorted(a["concept_id"] for a in ancestors),
        )

    return snapshot


# ============================================================
# Items
# ============================================================

@dataclass
class BenchmarkItem:
    item_id: str
    task: str
    concept_id: str
    gold: object                      # list of IDs (T1/T2) or bool (T3)
    target_id: str = None             # T3 only
    negative_type: str = None         # T3 negatives: "random"

    def to_dict(self):
        return asdict(self)


def generate_items(snapshot, seed=0):
    """Deterministic items for T1, T2 and T3 from a gold snapshot."""

    rng = random.Random(seed)
    items = []
    concept_ids = sorted(snapshot.concepts)

    for cid in concept_ids:
        concept = snapshot.concepts[cid]
        items.append(BenchmarkItem(f"T1-{cid}", "T1_direct_parents", cid, sorted(concept.parents)))

        if concept.children is not None and concept.children_complete:
            items.append(BenchmarkItem(f"T2-{cid}", "T2_direct_children", cid, sorted(concept.children)))

        if concept.ancestors:
            positive = rng.choice(sorted(concept.ancestors))
            items.append(BenchmarkItem(f"T3-{cid}-pos", "T3_is_a", cid, True, target_id=positive))

            excluded = set(concept.ancestors) | {cid}
            negatives = [
                other for other in concept_ids
                if other not in excluded
                and cid not in (snapshot.concepts[other].ancestors or [])
            ]
            if negatives:
                negative = rng.choice(negatives)
                items.append(BenchmarkItem(f"T3-{cid}-neg", "T3_is_a", cid, False,
                                           target_id=negative, negative_type="random"))

    return items


# ============================================================
# Reference system R0: answer from a local graph
# ============================================================

def answer_from_graph(items, graph, max_depth=20):
    """
    Answer items using only what ``graph`` stores (GraphRepository or any
    object with get_parents / get_children / get_ancestors).
    """
    predictions = {}
    for item in items:
        if item.task == "T1_direct_parents":
            predictions[item.item_id] = sorted(p["concept_id"] for p in graph.get_parents(item.concept_id))
        elif item.task == "T2_direct_children":
            predictions[item.item_id] = sorted(c["concept_id"] for c in graph.get_children(item.concept_id))
        elif item.task == "T3_is_a":
            ancestors = {a["concept_id"] for a in graph.get_ancestors(item.concept_id, max_depth=max_depth)}
            predictions[item.item_id] = item.target_id in ancestors
    return predictions


# ============================================================
# Scoring
# ============================================================

def evaluate(items, predictions):
    """
    Score predictions. A missing prediction counts as an empty answer (T1/T2)
    or as wrong (T3), and is counted in ``missing``.
    """

    report = {}
    for task in TASKS:
        task_items = [i for i in items if i.task == task]
        missing = sum(1 for i in task_items if i.item_id not in predictions)
        if task in ("T1_direct_parents", "T2_direct_children"):
            rows = [set_prf(predictions.get(i.item_id, []), i.gold) for i in task_items]
            report[task] = {"n": len(task_items), "missing": missing, **macro_average(rows)}
        else:
            predicted = [predictions.get(i.item_id) for i in task_items]
            gold = [i.gold for i in task_items]
            report[task] = {"n": len(task_items), "missing": missing, "accuracy": accuracy(predicted, gold)}
    return report


# ============================================================
# Command line: build a gold snapshot (LIVE, bounded)
# ============================================================

def _main(argv=None):
    parser = argparse.ArgumentParser(description="Build a frozen gold snapshot from BioPortal (live requests).")
    parser.add_argument("--ids", required=True, help="Text file with one SNOMED CT concept ID per line")
    parser.add_argument("--out", default=str(DEFAULT_GOLD_DIR / "snapshot.json"))
    parser.add_argument("--live", action="store_true", help="Required: confirms you want to call BioPortal")
    args = parser.parse_args(argv)

    if not args.live:
        parser.error("add --live to confirm that BioPortal will be contacted")

    from crawler.bioportal_client import BioPortalClient

    ids = Path(args.ids).read_text(encoding="utf-8").split()
    client = BioPortalClient()
    snapshot = build_gold_snapshot(client, ids)
    path = snapshot.save(args.out)
    print(f"Saved {len(snapshot.concepts)} concepts ({len(snapshot.failures)} failures) to {path}")
    print(f"BioPortal requests: {client.request_count}; release: {snapshot.version}")


if __name__ == "__main__":
    _main()
