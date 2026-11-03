"""
Live smoke test for an LLM provider (milestone M0).

It checks, with a handful of calls, that the provider:
* accepts our requests (key, endpoint, model name),
* returns JSON that ``llm.reasoner.parse_answer`` can read, for all five
  question types, using the real prompt and the real evidence renderer, and
* reports token usage and latency, which every experiment logs.

The evidence is a small SYNTHETIC hierarchy written in this file, so no
SNOMED CT content is sent to the provider. Results are smoke-test output,
not findings.

    python -m llm.smoke --provider gemini --list
    python -m llm.smoke --provider gemini --model <model-id>
    python -m llm.smoke --provider gemini --model <model-id> --no-json-mode
    python -m llm.smoke --provider gemini --model <model-id> --reasoning-effort none

Each run appends its raw responses to results/smoke/<model>.jsonl
(Git-ignored).
"""

import argparse
import json
import time
from pathlib import Path

from evaluation.conditions import Context
from evaluation.scoring import score_answer
from evaluation.sctid import make_concept_sctid
from evaluation.tasks import Item
from explanation.explainer import ReferenceHierarchy
from llm.client import LLMError
from llm.prompt_manager import PROMPT_VERSION, build_messages
from llm.providers import build_client
from llm.reasoner import parse_answer

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Synthetic concepts (valid-format IDs that are not real SNOMED CT concepts).
X, P1, P2, TOP, CHILD = (make_concept_sctid(n) for n in (9001, 9002, 9003, 9004, 9005))
LABELS = {
    X: "Synthetic inflammation of organ A",
    P1: "Synthetic inflammation",
    P2: "Synthetic disorder of organ A",
    TOP: "Synthetic disorder",
    CHILD: "Synthetic acute inflammation of organ A",
}
EDGES = {(X, P1), (X, P2), (P1, TOP), (P2, TOP), (CHILD, X)}


def smoke_context():
    """A complete small context: parents and children of X, ancestors to TOP."""
    return Context(
        condition="FULL", focal_id=X, nodes=dict(LABELS), edges=set(EDGES),
        parents_status={X: "complete", P1: "complete", P2: "complete", TOP: "complete"},
        children_status={X: "complete"},
    )


def smoke_items():
    return [
        Item("smoke:T1", "T1", X, sorted([P1, P2])),
        Item("smoke:T2", "T2", X, [CHILD]),
        Item("smoke:T3", "T3", X, True, target_id=TOP, polarity="pos"),
        Item("smoke:T4", "T4", X, None, target_id=TOP),
        Item("smoke:T5", "T5", X, None),
    ]


def smoke_reference():
    return ReferenceHierarchy(EDGES, nodes=set(LABELS), children_complete={X, CHILD},
                              parents_complete={X, P1, P2, TOP, CHILD})


def run_smoke(client, fmt="v2"):
    """Send the five smoke questions; return one result dict per question."""
    context, reference = smoke_context(), smoke_reference()
    text = context.render(fmt)
    rows = []
    for item in smoke_items():
        messages = build_messages(item, text, LABELS.get)
        row = {"item": item.item_id, "task": item.task, "prompt_version": PROMPT_VERSION}
        try:
            response = client.complete(messages)
        except LLMError as exc:
            row.update({"ok": False, "error": str(exc)})
            rows.append(row)
            continue
        parsed = parse_answer(response.text, item.task)
        scores = score_answer(item, parsed, context, reference)
        row.update({
            "ok": True, "response_model": response.model, "finish_reason": response.finish_reason,
            "prompt_tokens": response.prompt_tokens, "completion_tokens": response.completion_tokens,
            "latency_s": response.latency_s, "parse_ok": parsed["parse_error"] is None,
            "parse_error": parsed["parse_error"], "answer": parsed["answer"],
            "n_claims": scores["n_claims"], "n_unsupported": scores["n_claims"] - scores.get("n_supported_by_subgraph", 0),
            "n_cited_claims": sum(1 for c in parsed["claims"] if c.get("cites")),
            "score": scores.get("score"), "raw_text": response.text,
        })
        rows.append(row)
    return rows


def _print_rows(rows):
    for r in rows:
        if not r["ok"]:
            print(f"{r['task']}: REQUEST FAILED - {r['error']}")
            continue
        score = "n/a" if r["score"] is None else f"{r['score']:.2f}"
        print(f"{r['task']}: parse_ok={r['parse_ok']} finish={r['finish_reason']} score={score} "
              f"claims={r['n_claims']} cited={r['n_cited_claims']} unsupported={r['n_unsupported']} "
              f"tokens={r['prompt_tokens']}/{r['completion_tokens']} latency={r['latency_s']}s")
        if not r["parse_ok"]:
            print(f"    parse error: {r['parse_error']}; start of reply: {r['raw_text'][:160]!r}")
    ok = [r for r in rows if r["ok"]]
    parsed = [r for r in ok if r["parse_ok"]]
    print(f"Summary: {len(ok)}/{len(rows)} requests succeeded, {len(parsed)}/{len(rows)} parsed.")


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Live LLM provider smoke test (synthetic evidence).")
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--model", help="Model identifier (see --list)")
    parser.add_argument("--list", action="store_true", help="List the models this key can use")
    parser.add_argument("--no-json-mode", action="store_true", help="Do not request JSON output mode")
    parser.add_argument("--reasoning-effort", help="Sent as reasoning_effort (e.g. none, low) if given")
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--rpm", type=float, default=10, help="Requests per minute (stay under free-tier limits)")
    parser.add_argument("--max-retries", type=int, default=1,
                        help="Retries per request on 429/5xx (low by default to protect free-tier quota)")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    spec = {"name": f"smoke-{args.provider}", "provider": args.provider, "model": args.model or "unused",
            "temperature": 0.0, "max_tokens": args.max_tokens, "json_mode": not args.no_json_mode,
            "rpm": args.rpm, "max_retries": args.max_retries}
    if args.reasoning_effort:
        spec["extra_body"] = {"reasoning_effort": args.reasoning_effort}
    client = build_client(spec)

    if args.list:
        for model_id in client.list_models():
            print(model_id)
        return
    if not args.model:
        parser.error("--model is required (use --list to see the available models)")

    print(f"Provider {args.provider}, model {args.model}, json_mode={spec['json_mode']}, "
          f"reasoning_effort={args.reasoning_effort}, prompt {PROMPT_VERSION}. Synthetic evidence only.")
    rows = run_smoke(client)
    _print_rows(rows)

    out = PROJECT_ROOT / "results" / "smoke" / f"{args.model.replace('/', '_')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with out.open("a", encoding="utf-8") as handle:
        for r in rows:
            handle.write(json.dumps(dict(r, run_at=stamp, settings=client.request_fingerprint()),
                                    ensure_ascii=False) + "\n")
    print(f"Raw responses appended to {out}")


if __name__ == "__main__":
    _main()
