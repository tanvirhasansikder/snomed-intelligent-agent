"""Read-only viewer for the Amendment 6 error-analysis sample.

    python -m evaluation.error_review --list
    python -m evaluation.error_review --config configs/main.json --index 1

Rebuilds each sampled item's retrieved evidence with the runner's own
(snapshot-only) retrieval and prints it beside the reference answer and the
model's answer. It never writes anything; fill the review columns in
results/main/error_analysis/sample.csv yourself.
"""
import argparse
import csv
import inspect
import json
from pathlib import Path

SAMPLE = Path("results/main/error_analysis/sample.csv")
MAX_EDGES = 60


def read_sample():
    with SAMPLE.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def runner_class(module):
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if cls.__module__ == module.__name__ and hasattr(cls, "run_systems"):
            return cls
    raise SystemExit("Could not find the runner class in evaluation.system_runner.")


def parse(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def show(row, number, total, config_path):
    from evaluation import system_runner
    from evaluation.collect import load_config
    from evaluation.tasks import reference_answer
    from llm.prompt_manager import build_messages

    runner = runner_class(system_runner)(load_config(config_path))
    snapshot_type = type(runner.snapshot).__name__
    if "snapshot" not in snapshot_type.lower():
        raise SystemExit(f"Runner snapshot is {snapshot_type}, not a snapshot client; stopping so no live API is used.")

    def lab(value):
        text = str(value)
        if text.isdigit():
            try:
                return f"{runner.gold.label(text)} ({text})"
            except Exception:
                return text
        return text

    def edge_text(edge):
        try:
            if isinstance(edge, dict):
                return "  ".join(f"{k}={lab(v)}" for k, v in edge.items())
            return " -> ".join(lab(p) for p in edge)
        except TypeError:
            return repr(edge)

    items = {item.item_id: item for item in runner.items}
    item = items[row["item_id"]]
    context, requests = runner.system_context(item, row["system"])

    print("=" * 78)
    print(f"Sample item {number}/{total} | stratum {row['stratum']} | system {row['system']}")
    print(f"item_id {item.item_id} | task {item.task} | polarity {item.polarity}")
    print(f"concept: {lab(item.concept_id)}")
    if item.target_id:
        print(f"target : {lab(item.target_id)}")
    print(f"retrieval requests: {requests} | nodes {len(context.nodes)} | edges {len(context.edges)}")

    print("\n--- Question as sent (evidence omitted) ---")
    try:
        for message in build_messages(item, "<EVIDENCE OMITTED>", runner._label):
            if isinstance(message, dict) and message.get("role") == "user":
                print(str(message.get("content"))[:1500])
    except Exception as error:
        print(f"(could not render the prompt: {error})")

    print("\n--- Retrieved evidence ---")
    edges = list(context.edges.values()) if isinstance(context.edges, dict) else list(context.edges)
    for edge in edges[:MAX_EDGES]:
        print("  ", edge_text(edge))
    if len(edges) > MAX_EDGES:
        print(f"   ... {len(edges) - MAX_EDGES} more edges")

    reference = reference_answer(item, context)
    print("\n--- Reference (R0) answer on this evidence ---")
    print("  answer:", reference.get("answer"), "| insufficient:", reference.get("insufficient_evidence"))
    print("  gold (evaluation only):", item.gold)

    print("\n--- Model output ---")
    print("  answer:", row["answer"])
    print(f"  score {row['score']} | abstained {row['abstained']} | llm calls {row['n_llm_calls']}")
    print(f"  verifier: false_in_reference={row['n_false_in_reference']} unknown_identifier={row['n_unknown_identifier']}")
    claims = parse(row["claims"])
    for claim in claims if isinstance(claims, list) else [claims]:
        print("  claim:", edge_text(claim.values()) if isinstance(claim, dict) else claim)
    print("\nFill in sample.csv: reviewer_verdict, failure_category, verifier_label_correct, notes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--config", default="configs/main.json")
    parser.add_argument("--index", type=int)
    args = parser.parse_args()
    rows = read_sample()
    if args.list or args.index is None:
        for number, row in enumerate(rows, 1):
            print(f"{number:>2}  {row['stratum']:<26} {row['system']:<3} {row['task']:<3} score={row['score']}")
        return
    show(rows[args.index - 1], args.index, len(rows), args.config)


if __name__ == "__main__":
    main()
