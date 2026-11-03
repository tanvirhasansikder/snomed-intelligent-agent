"""
Collect the frozen research data set from BioPortal (protocol Section 3).

Steps (all calls go through the RecordingClient cache, so re-running after an
interruption resumes without repeating requests):

1. Record the SNOMEDCT release (``latest_submission``).
2. Validate that each configured top-level hierarchy is a child of the root.
3. Sample concepts per hierarchy by seeded random descent through direct
   children to a random target depth.
4. Collect each sampled concept's closure: label, parents, children,
   ancestors (BioPortal /ancestors), the parents of every ancestor, and the
   children of each direct parent (for sibling negatives).

Command line (live; bounded by the configuration):

    python -m evaluation.collect --config configs/pilot.json --live
    python -m evaluation.collect --config configs/dryrun.json --synthetic
"""

import argparse
import json
import logging
import random
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from crawler.bioportal_client import BioPortalError
from evaluation.bioportal_cache import CallStore, RecordingClient

logger = logging.getLogger(__name__)

ROOT_ID = "138875005"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Default hierarchies (protocol Section 3). IDs are validated against the
#: root's children at collection time.
DEFAULT_HIERARCHIES = {
    "404684003": "Clinical finding",
    "71388002": "Procedure",
    "123037004": "Body structure",
    "105590001": "Substance",
    "373873005": "Pharmaceutical / biologic product",
    "410607006": "Organism",
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_revision():
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def load_config(path):
    path = Path(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    config["_config_path"] = str(path)
    return config


def resolve(config, key):
    """Resolve a path setting relative to the project root."""
    return (PROJECT_ROOT / config[key]).resolve()


# ============================================================
# Sampling
# ============================================================

def validate_hierarchies(client, hierarchy_ids, root_id=ROOT_ID):
    top = {c["concept_id"] for c in client.fetch_children(root_id)["children"]}
    missing = [h for h in hierarchy_ids if h not in top]
    if missing:
        raise ValueError(f"Not direct children of the root {root_id}: {missing}")
    return sorted(top)


def sample_concepts(client, hierarchies, per_hierarchy, seed, min_depth=2, max_depth=8,
                    exclude=(), max_attempts_factor=20):
    """
    Seeded random descent sampler.

    For each hierarchy, repeatedly: draw a target depth uniformly from
    [min_depth, max_depth]; walk down from the top-level concept choosing a
    uniformly random direct child at each step; stop at the target depth or at
    a leaf. Concepts already chosen, in ``exclude``, or with an incomplete child
    list on the path are handled as described in the record fields.
    """

    excluded = set(exclude)
    chosen, samples = set(), []

    for index, (hierarchy_id, hierarchy_name) in enumerate(sorted(hierarchies.items())):
        rng = random.Random(f"{seed}:{hierarchy_id}")
        picked, attempts = 0, 0
        while picked < per_hierarchy:
            attempts += 1
            if attempts > per_hierarchy * max_attempts_factor:
                logger.warning("Hierarchy %s: only %d of %d concepts sampled", hierarchy_id, picked, per_hierarchy)
                break

            target = rng.randint(min_depth, max_depth)
            path = [hierarchy_id]
            truncated_on_path = False
            current = hierarchy_id
            for _ in range(target):
                fetched = client.fetch_children(current)
                children = sorted(fetched["children"], key=lambda c: c["concept_id"])
                if not fetched["complete"]:
                    truncated_on_path = True
                if not children:
                    break
                current = rng.choice(children)["concept_id"]
                path.append(current)

            if current == hierarchy_id or current in chosen or current in excluded:
                continue

            chosen.add(current)
            picked += 1
            samples.append({
                "concept_id": current,
                "hierarchy_id": hierarchy_id,
                "hierarchy_name": hierarchy_name,
                "target_depth": target,
                "descent_depth": len(path) - 1,
                "stopped_at_leaf": len(path) - 1 < target,
                "descent_path": path,
                "child_list_truncated_on_path": truncated_on_path,
            })

    return samples


# ============================================================
# Closure collection
# ============================================================

def collect_closure(client, concept_id):
    """
    Fetch everything the experiments and gold standard need for one concept.
    Returns a summary dict; raises BioPortalError on failure.
    """

    concept = client.get_concept(concept_id)
    if concept is None:
        raise BioPortalError(f"BioPortal has no SNOMEDCT class {concept_id}")

    parents = client.get_parents(concept_id)
    client.fetch_children(concept_id)
    ancestors = {a["concept_id"] for a in client.get_ancestors(concept_id)}

    # Parents of every ancestor reached through the parent links (BFS),
    # plus any ancestor reported by /ancestors that BFS did not reach.
    seen, queue = set(), deque(p["concept_id"] for p in parents)
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        for parent in client.get_parents(current):
            queue.append(parent["concept_id"])
    for extra in sorted(ancestors - seen):
        client.get_parents(extra)

    for parent in parents:
        client.fetch_children(parent["concept_id"])

    return {
        "concept_id": concept_id,
        "name": concept["name"],
        "n_parents": len(parents),
        "n_ancestors_bfs": len(seen),
        "n_ancestors_api": len(ancestors),
        "ancestors_consistent": seen == ancestors,
    }


def collect(config, live_client, store):
    """Run sampling and closure collection for a configuration."""

    client = RecordingClient(live_client, store,
                             max_child_pages=config.get("max_child_pages", 20),
                             child_pagesize=config.get("child_pagesize", 100))
    data = config["data"]
    hierarchies = data.get("hierarchies") or DEFAULT_HIERARCHIES

    version = client.get_ontology_version()
    validate_hierarchies(client, list(hierarchies))

    exclude = set()
    for other in data.get("exclude_samples_from", []):
        other_path = PROJECT_ROOT / other
        if other_path.exists():
            exclude.update(s["concept_id"] for s in json.loads(other_path.read_text(encoding="utf-8"))["samples"])

    samples = sample_concepts(
        client, hierarchies, data["per_hierarchy"], data["seed"],
        data.get("min_depth", 2), data.get("max_depth", 8), exclude=exclude,
    )

    failures, summaries = {}, []
    for sample in samples:
        try:
            summaries.append(collect_closure(client, sample["concept_id"]))
        except BioPortalError as exc:
            failures[sample["concept_id"]] = str(exc)
            logger.warning("Closure failed for %s: %s", sample["concept_id"], exc)

    ok_ids = {s["concept_id"] for s in summaries}
    first, last = store.retrieval_window()
    dataset = {
        "schema_version": "2.0",
        "name": config["name"],
        "synthetic": bool(config.get("synthetic", False)),
        "source": "synthetic" if config.get("synthetic") else "BioPortal REST API (SNOMEDCT)",
        "release": version,
        "collected_at": _utc_now(),
        "retrieval_window": {"first": first, "last": last},
        "code_revision": git_revision(),
        "config": {k: v for k, v in config.items() if not k.startswith("_")},
        "samples": [dict(s, **next(m for m in summaries if m["concept_id"] == s["concept_id"]))
                    for s in samples if s["concept_id"] in ok_ids],
        "failures": failures,
        "api_requests": client.request_count,
        "cache_records": len(store),
    }
    return dataset


def save_dataset(config, dataset):
    path = resolve(config, "dataset_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataset, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return path


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Collect the frozen research data set.")
    parser.add_argument("--config", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true", help="Call BioPortal (bounded by the configuration)")
    group.add_argument("--synthetic", action="store_true", help="Use the built-in synthetic ontology (no network)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    store = CallStore(resolve(config, "cache_path"))

    if args.synthetic:
        from evaluation.synthetic import SyntheticOntology
        config["synthetic"] = True
        live = SyntheticOntology(seed=config.get("synthetic_seed", 0))
        if not config["data"].get("hierarchies"):
            config["data"]["hierarchies"] = live.top_level_names()
    else:
        if config.get("synthetic"):
            parser.error("this configuration is synthetic; use --synthetic")
        from crawler.bioportal_client import BioPortalClient
        live = BioPortalClient()

    dataset = collect(config, live, store)
    path = save_dataset(config, dataset)
    print(f"Saved {len(dataset['samples'])} concepts ({len(dataset['failures'])} failures) to {path}")
    print(f"Release: {dataset['release']}; new API requests: {dataset['api_requests']}; "
          f"cache records: {dataset['cache_records']}")
    inconsistent = [s["concept_id"] for s in dataset["samples"] if not s["ancestors_consistent"]]
    if inconsistent:
        print(f"WARNING: /ancestors differs from the parent-link closure for {len(inconsistent)} concept(s): "
              f"{inconsistent[:10]}")


if __name__ == "__main__":
    _main()
