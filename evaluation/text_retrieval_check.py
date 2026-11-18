"""
Check the L-RAG or D-RAG baseline on real data before any model sees it (read-only, no API calls).

    python -m evaluation.text_retrieval_check --db data\\snomed_db\\snomed.db --dataset data\\datasets\\v3_pilot.json
    python -m evaluation.text_retrieval_check --system D-RAG --db data\\snomed_db\\snomed.db --dataset data\\datasets\\v3_pilot.json

For every item of the data set it runs the lexical (BM25) card retrieval and
the question-aware graph retrieval (G-QA = S1, with ``--budget`` requests) and reports, per task: whether
the question's own concept card was retrieved and at what rank, how many card
names could not be matched to an ID, how many IS_A links the evidence holds,
whether the evidence is sufficient, and the evidence length of both systems
(so the text baseline is not compared at a very different size). It also
saves the evidence of the first items to
data/snomed_db/text_retrieval_check.txt (private: contains SNOMED CT terms).
"""

import argparse
import logging
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from crawler.local_client import LocalTerminologyClient
from evaluation.bioportal_cache import CallStore
from evaluation.gold import GoldIndex
from evaluation.tasks import answer_in_evidence, generate_items, sufficient
from evaluation.text_retrieval import CARD_FORMAT, DEFAULT_K, CardStore, card_context, lexical_retrieve
from query_engine.retrieval_planner import build_s1_context

EXAMPLES = 6


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check L-RAG or D-RAG on real data (no API calls)")
    parser.add_argument("--db", default="data/snomed_db/snomed.db")
    parser.add_argument("--dataset", default="data/datasets/v3_pilot.json")
    parser.add_argument("--cache", default="data/local_cache/v3_calls.jsonl")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--budget", type=int, default=1000,
                        help="G-QA request budget per question (Study 1 used 60 for BioPortal)")
    parser.add_argument("--system", choices=("L-RAG", "D-RAG"), default="L-RAG")
    parser.add_argument("--dense-dir", default="data/snomed_db/dense",
                        help="D-RAG: folder with the exported matrix and the query vectors")
    parser.add_argument("--out", default=None, help="default: data/snomed_db/text_retrieval_check[_drag].txt")
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = "data/snomed_db/text_retrieval_check" + ("_drag" if args.system == "D-RAG" else "") + ".txt"

    started = time.time()
    client = LocalTerminologyClient.from_db(args.db)
    keep = set(client.graph.names)
    store = CardStore(args.db)
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    items = generate_items(GoldIndex(CallStore(args.cache), dataset), seed=0)
    if args.system == "D-RAG":
        from evaluation.dense_retrieval import DenseIndex, dense_retrieve, load_query_vectors
        index = DenseIndex(args.dense_dir)
        vectors = load_query_vectors(Path(args.dense_dir) / f"query_vectors_{Path(args.dataset).stem}.npz")

        def retrieve(item):
            return dense_retrieve(item, index, vectors, client.label, k=args.k)
    else:
        def retrieve(item):
            return lexical_retrieve(item, store, client.label, k=args.k, keep=keep)
    logging.getLogger("crawler").setLevel(logging.ERROR)    # budget refusals are counted below, not logged
    print(f"Loaded in {time.time() - started:.0f} s: {len(items)} items from {len(dataset['samples'])} concepts")

    rows, examples = defaultdict(list), []
    search_ms, requests, stopped = [], [], 0
    for item in items:
        t0 = time.perf_counter()
        ids, queries = retrieve(item)
        query = " | ".join(queries)
        search_ms.append((time.perf_counter() - t0) * 1000)
        text_ctx = card_context(item, ids, store, args.system)
        graph_ctx = build_s1_context(client, item, budget=args.budget)
        requests.append(graph_ctx.requests_used or 0)
        stopped += bool(graph_ctx.budget_stopped)
        text_ev, graph_ev = text_ctx.render(CARD_FORMAT), graph_ctx.render("v2")
        rows[item.task].append({
            "focal": item.concept_id in ids,
            "rank": ids.index(item.concept_id) + 1 if item.concept_id in ids else None,
            "target": (item.target_id in ids) if item.target_id else None,
            "unmatched": sum(c["unmatched"] for _, c in text_ctx.cards),
            "names": sum(len(c["parents"]) + len(c["children"]) + len(c["path"]) for _, c in text_ctx.cards),
            "edges": len(text_ctx.edges),
            "suff_text": sufficient(item, text_ctx), "suff_graph": sufficient(item, graph_ctx),
            "aie_text": answer_in_evidence(item, text_ctx),
            "chars_text": len(text_ev), "chars_graph": len(graph_ev),
        })
        if len(examples) < EXAMPLES and item.task not in {e[0] for e in examples}:
            examples.append((item.task, item.item_id, query, text_ev, graph_ev))

    lines = [f"{args.system} check on {args.dataset}: {len(items)} items, k = {args.k}",
             f"{'BM25' if args.system == 'L-RAG' else 'Dense'} search: median {statistics.median(search_ms):.0f} ms, max {max(search_ms):.0f} ms",
             f"G-QA requests per question (budget {args.budget}): median {statistics.median(requests):.0f}, "
             f"95th percentile {sorted(requests)[int(0.95 * (len(requests) - 1))]}, max {max(requests)}; "
             f"items stopped by the budget: {stopped}", "",
             "task  n   own card  median rank  2nd concept  unmatched names  IS_A links  "
             "sufficient text/graph  answer in text  chars text/graph (median)"]
    for task in sorted(rows):
        r = rows[task]
        n = len(r)
        ranks = [x["rank"] for x in r if x["rank"]]
        targets = [x["target"] for x in r if x["target"] is not None]
        names = sum(x["names"] for x in r)
        lines.append(
            f"{task:4}  {n:<3} {sum(x['focal'] for x in r):>3}/{n:<5} "
            f"{(statistics.median(ranks) if ranks else float('nan')):>6}       "
            f"{(f'{sum(targets)}/{len(targets)}' if targets else '-'):>7}      "
            f"{sum(x['unmatched'] for x in r):>5}/{names:<6}     {statistics.median(x['edges'] for x in r):>6}      "
            f"{sum(x['suff_text'] for x in r):>3}/{sum(x['suff_graph'] for x in r):<3}              "
            f"{sum(x['aie_text'] for x in r):>3}/{n:<3}         "
            f"{statistics.median(x['chars_text'] for x in r):>6.0f}/{statistics.median(x['chars_graph'] for x in r):.0f}")
    report = "\n".join(lines)
    print(report)

    detail = [report, ""]
    for task, item_id, query, text_ev, graph_ev in examples:
        detail += [f"===== {task} {item_id}", f"QUERY: {query}", f"--- {args.system} evidence ---", text_ev,
                   "--- G-QA evidence ---", graph_ev, ""]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(detail) + "\n", encoding="utf-8")
    print(f"\nExamples saved to {out} (private: do not commit)")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
