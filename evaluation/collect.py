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
    """
    Check the configured top-level hierarchies and say how each was confirmed:

    * "child_of_root": a direct child of the SNOMED CT root (the full
      SNOMED structure; also the synthetic test ontology)
    * "top_concept":   the concept exists and has NO parents. BioPortal's
      SNOMEDCT (US Edition via UMLS) exposes the top-level hierarchies
      themselves as its top concepts; the root 138875005 exists there but
      lists no children (checked live on 2026-11-05).

    Anything else is rejected. Returns {hierarchy_id: how}.
    """
    top = {c["concept_id"] for c in client.fetch_children(root_id)["children"]}
    how, problems = {}, []
    for h in hierarchy_ids:
        if h in top:
            how[h] = "child_of_root"
        elif client.get_concept(h) is None:
            problems.append(f"{h} (not found)")
        elif client.get_parents(h):
            problems.append(f"{h} (neither a child of the root {root_id} nor a top concept)")
        else:
            how[h] = "top_concept"
    if problems:
        raise ValueError(f"Invalid top-level hierarchies: {problems}")
    return how


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


DEFAULT_DEPTH_BINS = (("shallow", 1, 3), ("middle", 4, 6), ("deep", 7, 99))


def sample_concepts_stratified(graph, hierarchies, per_stratum, seed, depth_bins=DEFAULT_DEPTH_BINS,
                               exclude=(), max_per_hierarchy=None):
    """
    Seeded stratified sampler over a complete local graph (protocol v3, Study 2).

    For each hierarchy, every descendant of its top-level concept is placed in
    a stratum by (depth bin, leaf or not); depth is the shortest number of
    IS-A steps below the top-level concept. ``per_stratum`` concepts are drawn
    from each stratum (all of them if it has fewer). ``max_per_hierarchy``
    then optionally keeps a seeded subset (used for the small pilot).

    Each sample also gets a seeded shortest path from the top-level concept,
    so ``descent_path[-2]`` is a direct parent (the T2 subject).
    """
    excluded = set(exclude)
    depths = graph.depths()
    chosen, samples = set(), []
    for hierarchy_id, hierarchy_name in sorted(hierarchies.items()):
        rng = random.Random(f"{seed}:{hierarchy_id}")
        members, stack = set(), list(graph.children_of(hierarchy_id))
        while stack:
            node = stack.pop()
            if node not in members:
                members.add(node)
                stack.extend(graph.children_of(node))
        top_depth = depths[hierarchy_id][0]
        strata = {}
        for cid in sorted(members):
            if cid in excluded or cid in chosen or cid not in depths:
                continue
            depth = depths[cid][0] - top_depth
            bin_name = next((name for name, low, high in depth_bins if low <= depth <= high), None)
            if bin_name is None:
                continue
            leaf = not graph.children_of(cid)
            strata.setdefault((bin_name, leaf), []).append((cid, depth, leaf))
        picked = []
        for key in sorted(strata, key=lambda k: ([b[0] for b in depth_bins].index(k[0]), k[1])):
            pool = strata[key]
            if len(pool) < per_stratum:
                logger.warning("Hierarchy %s, stratum %s: only %d concepts", hierarchy_id, key, len(pool))
            picked.extend(rng.sample(pool, min(per_stratum, len(pool))))
        if max_per_hierarchy is not None and len(picked) > max_per_hierarchy:
            picked = rng.sample(picked, max_per_hierarchy)
        for cid, depth, leaf in sorted(picked):
            chosen.add(cid)
            path = [cid]
            while path[-1] != hierarchy_id:
                ups = [p for p in graph.parents_of(path[-1]) if p == hierarchy_id or p in members]
                best = min(depths[p][0] for p in ups)
                path.append(rng.choice(sorted(p for p in ups if depths[p][0] == best)))
            path.reverse()
            samples.append({
                "concept_id": cid,
                "hierarchy_id": hierarchy_id,
                "hierarchy_name": hierarchy_name,
                "sampler": "stratified-v3",
                "depth_bin": next(name for name, low, high in depth_bins if low <= depth <= high),
                "depth_below_top": depth,
                "is_leaf": leaf,
                "descent_path": path,
                # fields kept for compatibility with Study 1's records
                "target_depth": depth,
                "descent_depth": len(path) - 1,
                "stopped_at_leaf": leaf,
                "child_list_truncated_on_path": False,
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
        client.get_concept(parent["concept_id"])   # a T2 question can start at a direct parent (Amendment 4)

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
    hierarchy_validation = validate_hierarchies(client, list(hierarchies))

    exclude = set()
    for other in data.get("exclude_samples_from", []):
        other_path = PROJECT_ROOT / other
        if other_path.exists():
            exclude.update(s["concept_id"] for s in json.loads(other_path.read_text(encoding="utf-8"))["samples"])

    if data.get("sampler") == "stratified":
        graph = getattr(live_client, "graph", None)
        if graph is None:
            raise ValueError("the stratified sampler needs the local client (run with --local)")
        bins = tuple(tuple(b) for b in data["depth_bins"]) if data.get("depth_bins") else DEFAULT_DEPTH_BINS
        samples = sample_concepts_stratified(graph, hierarchies, data["per_stratum"], data["seed"], bins,
                                             exclude=exclude, max_per_hierarchy=data.get("max_per_hierarchy"))
    else:
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
        "source": ("synthetic" if config.get("synthetic") else
                   f"local snomed.db ({live_client.graph.view} view)" if hasattr(live_client, "graph") else
                   "BioPortal REST API (SNOMEDCT)"),
        "release": version,
        "hierarchy_validation": hierarchy_validation,
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
    group.add_argument("--local", metavar="DB", help="Use the local SNOMED CT database (no network), e.g. "
                                                     "data/snomed_db/snomed.db")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    store = CallStore(resolve(config, "cache_path"))

    if args.local:
        from crawler.local_client import LocalTerminologyClient
        live = LocalTerminologyClient.from_db(args.local, view=config["data"].get("view", "international"))
    elif args.synthetic:
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
    if any(s.get("sampler") == "stratified-v3" for s in dataset["samples"]):
        from collections import Counter
        counts = Counter((s["hierarchy_name"], s["depth_bin"], "leaf" if s["is_leaf"] else "non-leaf")
                         for s in dataset["samples"])
        for key, n in sorted(counts.items()):
            print(f"   {key[0]:<36} {key[1]:<8} {key[2]:<9} {n}")
    inconsistent = [s["concept_id"] for s in dataset["samples"] if not s["ancestors_consistent"]]
    if inconsistent:
        print(f"WARNING: /ancestors differs from the parent-link closure for {len(inconsistent)} concept(s): "
              f"{inconsistent[:10]}")


if __name__ == "__main__":
    _main()
