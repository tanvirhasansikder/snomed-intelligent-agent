"""
Item-level error review for Study 2: a sampled workbook to annotate, and its scoring.

    python -m evaluation.study2_error_review results\\v3_cse499                 # annotator 1 (with hints)
    python -m evaluation.study2_error_review results\\v3_cse499 --blind         # annotator 2 (no hints, no labels)
    python -m evaluation.study2_error_review results\\v3_cse499 --score A.xlsx [B.xlsx]

Sampling (fixed seed, reproducible): from each model condition, up to
``--per-condition`` answers that need review (wrong, a false or invented
claim, a claim from memory, or declined although the evidence sufficed),
plus ``--controls`` correct answers per condition, so the annotator cannot
assume every row is an error. Rows are shuffled with the same seed.

The workbook (``analysis_study2/error_review[_blind].xlsx``) has one row per
answer with the question, the reference answer, the model's answer and its
claims written out with concept names (read from snomed.db when it is
present), and empty columns to fill: a primary code, an optional secondary
code and a note. The codebook is on its own sheet. The blind version hides
the condition, the automatic flags and the claim labels, so a second
annotator judges the answer, not the system.

``--score`` reads one or two annotated workbooks and writes
``error_review_codes.csv`` (code counts per condition) and, for two, Cohen's
kappa on the primary code over the rows both annotators coded.
"""

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from evaluation.study2_report import CONDITION_ORDER

SEED = 2026111904
CODES = [
    ("E1", "Answered from memory", "The evidence lacked the needed links and the model filled them in from its own knowledge instead of declining."),
    ("E2", "Silent incompleteness", "A partial answer (e.g. some of the children) given as if complete; the evidence showed or marked it as partial."),
    ("E3", "Invented identifier", "A concept ID that does not exist in the release, or the wrong ID for the named concept."),
    ("E4", "Wrong level", "Confuses a direct parent or child with a more distant ancestor or descendant (or the reverse)."),
    ("E5", "Misread evidence", "The evidence contained the answer, but the model contradicted or misattributed it."),
    ("E6", "Over-closure", "Answers 'no' or 'none' that the incomplete evidence cannot certify."),
    ("E7", "Over-cautious decline", "Declined although the evidence was sufficient to answer."),
    ("E8", "Reference or scoring issue", "The answer looks right; the gold answer, mask or scorer is the problem. Explain in the note."),
    ("E9", "Other", "None of the above. Describe in the note."),
    ("OK", "No error", "The answer and its claims are acceptable."),
]
CODE_IDS = [c[0] for c in CODES]
RELATION = {"direct_parent": "is a direct child of", "ancestor": "is a kind of", "no_children": "has no children",
            "no_parents": "has no parents"}
LABEL = {"supported_by_subgraph": "supported", "true_not_retrieved": "true, from memory", "false_in_reference": "FALSE",
         "contradicted_by_complete_evidence": "CONTRADICTED", "unknown_identifier": "UNKNOWN ID",
         "insufficient_evidence": "undecidable"}
QUESTION = {"T1": "Direct parents of {x}", "T2": "Direct children of {x}", "T3": "Is {x} a kind of {y}?",
            "T4": "IS_A path from {x} up to {y}", "T5": "Where {x} sits in the hierarchy",
            "T6": "Nearest common ancestor of {x} and {y}"}


# ----------------------------------------------------------------------
# selection
# ----------------------------------------------------------------------

def outcome(row):
    if row.get("abstained"):
        return "declined"
    return "correct" if (row.get("score") or 0) >= 0.999 else "wrong"


def flags(row):
    f = []
    if outcome(row) == "wrong":
        f.append("wrong")
    if row.get("n_false_in_reference") or row.get("n_contradicted_by_complete_evidence"):
        f.append("false claim")
    if row.get("n_unknown_identifier"):
        f.append("unknown ID")
    if row.get("n_true_not_retrieved"):
        f.append("from memory")
    if row.get("abstained") and row.get("sufficient"):
        f.append("declined, evidence sufficed")
    return f


def hint(row, graph=None):
    """A suggested code for annotator 1 only (never shown in the blind workbook)."""
    f = flags(row)
    claims = row.get("claims") or []
    if "unknown ID" in f:
        return "E3"
    if "declined, evidence sufficed" in f:
        return "E7"
    if graph is not None:
        for c in claims:
            if c.get("label") == "false_in_reference" and c.get("relation") == "direct_parent" and c.get("object") \
                    and c["object"] in graph and c.get("subject") in graph and c["object"] in graph.ancestors(c["subject"]):
                return "E4"
    if row["task"] == "T3" and row.get("answer") == "no" and not row.get("sufficient") and outcome(row) == "wrong":
        return "E6"
    if row["task"] in ("T1", "T2") and outcome(row) == "wrong" and not row.get("abstained") \
            and claims and all(c.get("label") == "supported_by_subgraph" for c in claims):
        return "E2"
    if "from memory" in f and not row.get("sufficient"):
        return "E1"
    if "false claim" in f and row.get("answer_in_evidence"):
        return "E5"
    return "OK" if not f else ""


def select(rows, per_condition=12, controls=3, seed=SEED):
    """The review sample: reproducible for a given set of rows and seed."""
    rng = random.Random(seed)
    by_condition = defaultdict(list)
    for r in rows:
        if r["model_name"] != "R0" and r.get("error") is None and r.get("repetition", 0) == 0:
            by_condition[r["system"]].append(r)
    picked = []
    for condition in sorted(by_condition, key=lambda s: (CONDITION_ORDER.index(s) if s in CONDITION_ORDER else 99, s)):
        group = sorted(by_condition[condition], key=lambda r: r["item_id"])
        flagged = [r for r in group if flags(r)]
        clean = [r for r in group if not flags(r)]
        picked += rng.sample(flagged, min(per_condition, len(flagged)))
        picked += rng.sample(clean, min(controls, len(clean)))
    rng.shuffle(picked)
    return picked


# ----------------------------------------------------------------------
# workbook
# ----------------------------------------------------------------------

class Names:
    def __init__(self, db_path):
        self.graph = None
        if db_path and Path(db_path).exists():
            from knowledge_graph.snomed_db_loader import load_graph
            self.graph = load_graph(db_path)

    def __call__(self, concept_id):
        if not concept_id:
            return ""
        if self.graph is not None and concept_id in self.graph:
            return f"{self.graph.name(concept_id) or concept_id} [{concept_id}]"
        return f"[{concept_id}]" if self.graph is None else f"[{concept_id}] (not in the release)"

    def gold(self, row):
        if self.graph is None or row["task"] == "T5":
            return ""
        try:
            from ui.ask_verify import make_item
            g = make_item(self.graph, row["task"], row["concept_id"], row.get("target_id")).gold
        except Exception as exc:                       # noqa: BLE001  (shown in the cell, not fatal)
            return f"(could not compute: {exc})"
        if g is None:
            return "any valid IS_A path from X up to Y"
        return g if isinstance(g, str) else "; ".join(self(c) for c in g)


def _answer_text(row, names):
    a = row.get("answer")
    if row.get("abstained"):
        return "(declined: evidence insufficient)"
    if isinstance(a, list):
        return "; ".join(names(c) for c in a) if a else "(empty list)"
    return "" if a is None else str(a)


def _claims_text(row, names, blind):
    lines = []
    for c in row.get("claims") or []:
        text = f"{names(c.get('subject'))} {RELATION.get(c.get('relation'), c.get('relation'))}"
        if c.get("object"):
            text += f" {names(c['object'])}"
        text += f"  [model: {'evidence' if c.get('source') == 'evidence' else 'own knowledge'}"
        if c.get("cites"):
            text += f", cites {', '.join(c['cites'])}"
        text += "]"
        if not blind:
            text += f"  -> {LABEL.get(c.get('label'), c.get('label'))}"
        lines.append(text)
    return "\n".join(lines)


def _evidence_note(row):
    if row.get("base_system") == "B0":
        return "No evidence (closed book)."
    parts = [f"{row.get('context_edges', '?')} links shown"]
    if row.get("mask_n_withheld"):
        parts.append(f"{row['mask_n_withheld']} of {row['mask_n_relevant']} needed links withheld")
    parts.append("completeness marked" if row.get("variant") != "unmarked" else "no completeness marks")
    parts.append("evidence sufficient" if row.get("sufficient") else "evidence insufficient")
    return "; ".join(parts) + "."


def write_workbook(results_dir, db_path=None, per_condition=12, controls=3, seed=SEED, blind=False):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    results_dir = Path(results_dir)
    rows = [json.loads(line) for line in open(results_dir / "system_responses.jsonl", encoding="utf-8")]
    sample = select(rows, per_condition, controls, seed)
    names = Names(db_path)
    out = results_dir / "analysis_study2"
    out.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Review"
    head = ["review_id", "item_id", "task", "question", "reference_answer", "model_answer", "claims"]
    if blind:
        head.remove("item_id")              # e.g. "T3+:..." would give away the answer
    else:
        head[1:1] = ["condition"]
        head += ["evidence", "outcome", "score", "auto_flags", "suggested_code"]
    head += ["primary_code", "secondary_code", "note"]
    ws.append(head)
    for i, r in enumerate(sample, 1):
        q = QUESTION[r["task"]].format(x=names(r["concept_id"]), y=names(r.get("target_id")))
        values = {"review_id": f"R{i:03d}", "item_id": r["item_id"], "condition": r["system"], "task": r["task"],
                  "question": q, "reference_answer": names.gold(r), "model_answer": _answer_text(r, names),
                  "claims": _claims_text(r, names, blind), "evidence": _evidence_note(r), "outcome": outcome(r),
                  "score": None if r.get("score") is None else round(r["score"], 3),
                  "auto_flags": ", ".join(flags(r)), "suggested_code": hint(r, names.graph)}
        ws.append([values.get(h, "") for h in head])

    widths = {"review_id": 9, "item_id": 22, "condition": 18, "task": 6, "question": 40, "reference_answer": 40,
              "model_answer": 40, "claims": 70, "evidence": 34, "outcome": 10, "score": 7, "auto_flags": 22,
              "suggested_code": 10, "primary_code": 12, "secondary_code": 12, "note": 40}
    fill = PatternFill("solid", fgColor="FFF6D6")
    for col, h in enumerate(head, 1):
        letter = ws.cell(1, col).column_letter
        ws.column_dimensions[letter].width = widths.get(h, 14)
        ws.cell(1, col).font = Font(name="Arial", bold=True)
        for row in range(2, len(sample) + 2):
            cell = ws.cell(row, col)
            cell.font = Font(name="Arial")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if h in ("primary_code", "secondary_code", "note"):
                cell.fill = fill
    ws.freeze_panes = "C2"
    rule = DataValidation(type="list", formula1=f'"{",".join(CODE_IDS)}"', allow_blank=True)
    ws.add_data_validation(rule)
    for h in ("primary_code", "secondary_code"):
        letter = ws.cell(1, head.index(h) + 1).column_letter
        rule.add(f"{letter}2:{letter}{len(sample) + 1}")

    cb = wb.create_sheet("Codebook")
    cb.append(["code", "name", "use it when"])
    for c in CODES:
        cb.append(list(c))
    for col, w in zip("ABC", (8, 26, 100)):
        cb.column_dimensions[col].width = w
    for row in cb.iter_rows():
        for cell in row:
            cell.font = Font(name="Arial", bold=cell.row == 1)
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    about = wb.create_sheet("How to fill in")
    for line in (
        "Fill in only the yellow columns. One primary code per row (required), a secondary code if a second error is present, and a note for E8 and E9.",
        "Code what the answer does wrong, judged against the reference answer and the release, not against what the model could know.",
        "Some rows are correct answers on purpose; code them OK.",
        "Do not discuss rows with the other annotator until both of you have finished." if blind else
        "suggested_code is an automatic guess from the scorer's labels. Check it; change it whenever it is wrong.",
        f"Sample: seed {seed}, up to {per_condition} rows needing review and {controls} correct rows per condition, shuffled.",
        f"Source: {results_dir.name}, {len(rows)} recorded answers. Names {'from snomed.db' if names.graph is not None else 'not available (snomed.db not found): IDs only'}.",
    ):
        about.append([line])
    about.column_dimensions["A"].width = 130
    for row in about.iter_rows():
        row[0].font = Font(name="Arial")
        row[0].alignment = Alignment(wrap_text=True)

    path = out / ("error_review_blind.xlsx" if blind else "error_review.xlsx")
    wb.save(path)
    with open(out / "error_review_sample.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["review_id", "item_id", "condition", "model"])
        for i, r in enumerate(sample, 1):
            writer.writerow([f"R{i:03d}", r["item_id"], r["system"], r["model_name"]])
    return {"path": path, "rows": len(sample), "names": names.graph is not None}


# ----------------------------------------------------------------------
# scoring
# ----------------------------------------------------------------------

def read_codes(path):
    from openpyxl import load_workbook
    ws = load_workbook(path, read_only=True)["Review"]
    rows = list(ws.iter_rows(values_only=True))
    head = list(rows[0])
    out = {}
    for r in rows[1:]:
        rec = dict(zip(head, r))
        code = (rec.get("primary_code") or "").strip().upper()
        if rec.get("review_id") and code:
            out[rec["review_id"]] = {"primary": code, "secondary": (rec.get("secondary_code") or "").strip().upper()}
    return out


def cohen_kappa(a, b):
    """Cohen's kappa for two lists of labels of equal length."""
    n = len(a)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def score(results_dir, first, second=None):
    results_dir = Path(results_dir)
    sample = {r["review_id"]: r for r in csv.DictReader(open(results_dir / "analysis_study2" / "error_review_sample.csv", encoding="utf-8"))}
    a = read_codes(first)
    counts = defaultdict(Counter)
    for rid, codes in a.items():
        condition = sample.get(rid, {}).get("condition", "?")
        counts[condition][codes["primary"]] += 1
    out = results_dir / "analysis_study2" / "error_review_codes.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["condition", "n"] + CODE_IDS)
        for condition in sorted(counts, key=lambda s: (CONDITION_ORDER.index(s) if s in CONDITION_ORDER else 99, s)):
            c = counts[condition]
            writer.writerow([condition, sum(c.values())] + [c[k] for k in CODE_IDS])
    info = {"coded": len(a), "of": len(sample), "out": out, "kappa": None, "both": 0}
    if second:
        b = read_codes(second)
        both = sorted(set(a) & set(b))
        info["both"] = len(both)
        info["kappa"] = cohen_kappa([a[r]["primary"] for r in both], [b[r]["primary"] for r in both])
        info["agreement"] = (sum(a[r]["primary"] == b[r]["primary"] for r in both) / len(both)) if both else None
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description="Study 2 error review: sampled workbook and its scoring (no API calls)")
    parser.add_argument("results_dir", nargs="?", default="results/v3_cse499")
    parser.add_argument("--db", default="data/snomed_db/snomed.db", help="for concept names and reference answers")
    parser.add_argument("--per-condition", type=int, default=12)
    parser.add_argument("--controls", type=int, default=3)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--blind", action="store_true", help="the second annotator's version: no condition, flags or labels")
    parser.add_argument("--score", nargs="+", metavar="XLSX", help="score one or two annotated workbooks instead")
    args = parser.parse_args(argv)
    if args.score:
        info = score(args.results_dir, args.score[0], args.score[1] if len(args.score) > 1 else None)
        print(f"{info['coded']} of {info['of']} rows coded -> {info['out']}")
        if info["kappa"] is not None:
            print(f"both annotators: {info['both']} rows, agreement {info['agreement']:.2f}, Cohen's kappa {info['kappa']:.2f}")
        return 0
    info = write_workbook(args.results_dir, args.db, args.per_condition, args.controls, args.seed, args.blind)
    print(f"{info['rows']} answers to review -> {info['path']}")
    if not info["names"]:
        print("NOTE: snomed.db not found, so the workbook shows IDs only. Pass --db with its path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
