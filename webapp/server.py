"""
Web API and server for the SNOMED CT Intelligent Explorer web app.

    pip install -r requirements-web.txt
    python -m webapp.server            # then open http://localhost:8000

Serves the built frontend (webapp/static, built from webapp/frontend) and a
JSON API over the local snomed.db (International view) and the Study 2
pipeline (ui/ask_verify.py). Everything runs on this machine; only "Ask"
calls the model provider's API, and answers are cached.

Paths can be overridden with environment variables (used by the tests):
SNOMED_DB, SNOMED_DENSE_DIR, SNOMED_DEMO_CONFIG, SNOMED_DEMO_CACHE,
SNOMED_RESULTS_DIR.
"""

import csv
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config.settings  # noqa: F401  (loads .env: GEMINI_API_KEY)
from crawler.local_client import RELEASE, split_fsn
from evaluation.tasks import reference_answer
from evaluation.text_retrieval import parse_card
from knowledge_graph.snomed_db_loader import ROOT_ID
from ui.ask_verify import (
    NEEDS_TARGET, SYSTEM_HELP, SYSTEMS, TASK_TEXT, AskVerify, DemoError, ReadThroughCache, describe_label, make_item,
)

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get("SNOMED_DB", ROOT / "data" / "snomed_db" / "snomed.db"))
DENSE = Path(os.environ.get("SNOMED_DENSE_DIR", ROOT / "data" / "snomed_db" / "dense"))
CONFIG = Path(os.environ.get("SNOMED_DEMO_CONFIG", ROOT / "configs" / "v3_cse499.json"))
STUDY_CACHE = ROOT / "results" / "llm_cache_v3.jsonl"
DEMO_CACHE = Path(os.environ.get("SNOMED_DEMO_CACHE", ROOT / "results" / "llm_cache_demo.jsonl"))
RESULTS = Path(os.environ.get("SNOMED_RESULTS_DIR", ROOT / "results"))
STATIC = Path(__file__).resolve().parent / "static"

REFERENCE_MODEL = "reference"          # the deterministic evidence-only answer (R0): no model, no quota
CHILD_PAGE = 50
COMPARE_SYSTEMS = ("B0", "L-RAG", "D-RAG", "G-QA", "G-QA+V")
LINEAGE_CHILDREN = 12


# ----------------------------------------------------------------------
# state, loaded once in the background so the page can show progress
# ----------------------------------------------------------------------

class State:
    def __init__(self):
        self.demo = None
        self.error = None
        self.started = time.time()
        self.loaded_at = None
        self.top_level = {}
        self.top_level_sizes = {}
        self._llms = {}
        self._lock = threading.Lock()

    def load(self):
        if not DB.exists():
            self.error = f"snomed.db is not at {DB}. Put it there and restart."
            return
        try:
            from crawler.local_client import LocalTerminologyClient
            vectors = {}
            if DENSE.exists():
                from evaluation.dense_retrieval import load_query_vectors
                for path in sorted(DENSE.glob("query_vectors_*.npz")):
                    vectors.update(load_query_vectors(path))

            def encoder():
                from evaluation.dense_retrieval import load_encoder
                return load_encoder()

            client = LocalTerminologyClient.from_db(DB)
            self.demo = AskVerify(client, DB, dense_dir=DENSE if (DENSE / "ids.json").exists() else None,
                                  query_vectors=vectors, encoder_loader=encoder)
            graph = self.demo.graph
            self.top_level = {c: client.label(c) for c in graph.top_level_concepts()}
            self.top_level_sizes = {c: graph.descendant_count(c) for c in self.top_level}
            self.loaded_at = time.time()
        except Exception as exc:                      # shown on the status endpoint
            self.error = f"{type(exc).__name__}: {exc}"

    def require(self):
        if self.error:
            raise HTTPException(500, f"The database could not be loaded: {self.error}")
        if self.demo is None:
            raise HTTPException(503, "Still loading the SNOMED CT graph. Try again in a few seconds.")
        return self.demo

    def models(self):
        try:
            names = [m["name"] for m in json.loads(CONFIG.read_text(encoding="utf-8"))["models"]]
        except (OSError, ValueError, KeyError):
            names = []
        return [REFERENCE_MODEL] + names

    def llm(self, name):
        with self._lock:
            if name not in self._llms:
                from llm.client import CachedClient
                from llm.providers import build_client
                specs = {m["name"]: m for m in json.loads(CONFIG.read_text(encoding="utf-8"))["models"]}
                if name not in specs:
                    raise HTTPException(400, f"Unknown model {name}")
                cache = ReadThroughCache(DEMO_CACHE, STUDY_CACHE if STUDY_CACHE.exists() else None)
                self._llms[name] = CachedClient(build_client(specs[name]), cache)
            return self._llms[name]


state = State()


@asynccontextmanager
async def _lifespan(_app):
    if state.demo is None and state.error is None:
        threading.Thread(target=state.load, daemon=True).start()
    yield


app = FastAPI(title="SNOMED CT Intelligent Explorer", version="1.0", lifespan=_lifespan)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _concept_ref(demo, concept_id):
    fsn = demo.graph.name(concept_id)
    name, tag = split_fsn(fsn) if fsn else (concept_id, None)
    return {"id": concept_id, "name": name or concept_id, "tag": tag}


def _hierarchy_of(demo, concept_id):
    if concept_id in state.top_level:
        return {"id": concept_id, "name": state.top_level[concept_id]}
    for top in demo.graph.ancestors(concept_id) & set(state.top_level):
        return {"id": top, "name": state.top_level[top]}
    return None


def _depth(demo, concept_id):
    """(shortest, longest) IS_A steps from the root, by search over the concept's ancestors only."""
    graph = demo.graph
    nodes = graph.ancestors(concept_id) | {concept_id}
    shortest, longest = {}, {}

    def walk(node):
        if node in longest:
            return
        parents = [p for p in graph.parents_of(node) if p in nodes]
        if not parents:
            shortest[node] = longest[node] = 0
            return
        for p in parents:
            walk(p)
        shortest[node] = 1 + min(shortest[p] for p in parents)
        longest[node] = 1 + max(longest[p] for p in parents)

    import sys
    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
    walk(concept_id)
    return shortest[concept_id], longest[concept_id]


def _shortest_path(demo, concept_id):
    """One shortest IS_A path from the root down to the concept (for the breadcrumb)."""
    graph = demo.graph
    previous, frontier = {concept_id: None}, [concept_id]
    while frontier:
        nxt = []
        for node in frontier:
            if node == ROOT_ID or not graph.parents_of(node):
                path = [node]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])
                return [_concept_ref(demo, n) for n in path]
            for p in sorted(graph.parents_of(node)):
                if p not in previous:
                    previous[p] = node
                    nxt.append(p)
        frontier = nxt
    return [_concept_ref(demo, concept_id)]


def _card_details(demo, concept_id):
    text = demo.store.card(concept_id) or ""
    fields = parse_card(text)
    skip = {"Term", "Type", "Synonyms", "Parent concepts", "Hierarchy", "Includes"}
    attributes = [{"name": f, "value": v} for f, v in fields if f and f not in skip]
    values = dict(fields)
    synonyms = [s.strip() for s in (values.get("Synonyms") or "").split(";") if s.strip()]
    return {"synonyms": synonyms, "attributes": attributes, "card_text": text}


def _named(demo, value):
    if isinstance(value, list):
        return [_concept_ref(demo, v) if v in demo.graph else {"id": v, "name": v, "tag": None} for v in value]
    return value


def _claim_json(demo, claim):
    short, long = describe_label(claim["label"])
    subject = claim.get("subject")
    obj = claim.get("object")
    return {
        "subject": _concept_ref(demo, subject) if subject in demo.graph else {"id": subject, "name": subject},
        "relation": claim.get("relation"),
        "object": (_concept_ref(demo, obj) if obj in demo.graph else {"id": obj, "name": obj}) if obj else None,
        "source": claim.get("source"), "cites": list(claim.get("cites") or []),
        "label": claim["label"], "label_short": short, "label_long": long, "citation": claim.get("citation"),
    }


def _outcome(result):
    if result.error:
        return "error"
    if result.abstained:
        return "abstained"
    score = (result.score or {}).get("score")
    if score is None:
        return "answered"
    return "correct" if score >= 0.999 else ("partly correct" if score > 0 else "wrong")


def _result_json(demo, result, context_edges):
    item = result.item
    return {
        "system": result.system, "system_help": SYSTEM_HELP.get(result.system),
        "withheld": round(result.fraction * 100), "marked": result.marked,
        "question": {"task": item.task, "text": TASK_TEXT[item.task], "x": _concept_ref(demo, item.concept_id),
                     "y": _concept_ref(demo, item.target_id) if item.target_id else None,
                     "gold": _named(demo, item.gold)},
        "evidence": {"format": result.render_format, "text": result.evidence_text, "edges": context_edges,
                     "sufficient": result.sufficient, "answer_in_evidence": result.answer_in_evidence,
                     "mask": result.mask and {"n_relevant": result.mask["n_relevant"], "n_withheld": result.mask["n_withheld"],
                                              "withheld_edges": [{"child": _concept_ref(demo, c), "parent": _concept_ref(demo, p)}
                                                                 for c, p in result.mask["withheld_edges"]]},
                     "text_search": result.text_record},
        "error": result.error, "outcome": _outcome(result),
        "answer": _named(demo, result.answer), "abstained": result.abstained,
        "claims": [_claim_json(demo, c) for c in result.claims],
        "verification": {"regenerated": result.regenerated, "first_answer": _named(demo, result.first_answer),
                         "first_claims": [_claim_json(demo, c) for c in result.first_claims],
                         "removed": len(result.removed_claims)} if result.system == "G-QA+V" else None,
        "score": result.score and {k: result.score.get(k) for k in ("score", "precision", "recall", "abstained")},
        "cached": result.cached, "tokens": result.tokens, "latency_s": result.latency_s,
        "raw_text": result.raw_text,
    }


def _with_model(body, model):
    body["model"] = model
    return body


class _ReferenceLLM:
    """Answers with the deterministic evidence-only reference (R0): no model call."""

    def __init__(self, demo, item, context):
        self.reply = reference_answer(item, context)

    def complete(self, messages):
        from llm.client import LLMResponse
        return LLMResponse(text=json.dumps({"answer": self.reply["answer"], "claims": self.reply["claims"],
                                            "insufficient_evidence": self.reply["insufficient_evidence"]}),
                           model="reference (no model)", prompt_tokens=0, completion_tokens=0, latency_s=0.0,
                           cached=True)


def _ask(demo, req):
    try:
        item = make_item(demo.graph, req.task, req.x, req.y)
        fraction = req.withheld / 100
        if req.model == REFERENCE_MODEL:
            context, _, _, _ = demo.evidence(item, req.system, fraction, req.marked)
            llm = _ReferenceLLM(demo, item, context)
        else:
            llm = state.llm(req.model)
        result = demo.ask(item, req.system, llm, fraction=fraction, marked=req.marked)
        context, _, _, _ = demo.evidence(item, req.system, fraction, req.marked)
    except DemoError as exc:
        raise HTTPException(400, str(exc))
    edges = [{"id": eid, "child": _concept_ref(demo, c), "parent": _concept_ref(demo, p)}
             for eid, (c, p) in context.edge_index().items()] if req.system != "B0" else []
    return _with_model(_result_json(demo, result, edges), req.model)


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------

@app.get("/api/status")
def status():
    ready = state.demo is not None
    body = {"ready": ready, "error": state.error, "release": f"SNOMED CT International Edition {RELEASE}",
            "view": "International", "models": state.models(), "reference_model": REFERENCE_MODEL,
            "systems": [{"id": s, "help": SYSTEM_HELP[s]} for s in SYSTEMS],
            "tasks": [{"id": t, "text": TASK_TEXT[t], "needs_y": t in NEEDS_TARGET} for t in TASK_TEXT],
            "loading_seconds": round(time.time() - state.started)}
    if ready:
        graph = state.demo.graph
        body.update({"concepts": len(graph.names), "edges": graph.edge_count(),
                     "hierarchies": [{"id": c, "name": n, "size": state.top_level_sizes.get(c, 0)} for c, n in sorted(state.top_level.items(), key=lambda kv: kv[1])],
                     "dense_available": state.demo.dense_dir is not None})
    return body


@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(10, ge=1, le=30)):
    demo = state.require()
    q = q.strip()
    if q.isdigit():
        return {"results": [dict(_concept_ref(demo, q), hierarchy=_hierarchy_of(demo, q))] if q in demo.graph else []}
    hits = demo.local.search_concept(q, pagesize=limit)
    return {"results": [dict(_concept_ref(demo, h["concept_id"]), hierarchy=_hierarchy_of(demo, h["concept_id"]))
                        for h in hits]}


@app.get("/api/concept/{concept_id}")
def concept(concept_id: str):
    demo = state.require()
    graph = demo.graph
    if concept_id not in graph:
        raise HTTPException(404, f"{concept_id} is not in the International view")
    children = sorted(graph.children_of(concept_id), key=lambda c: (graph.name(c) or "").lower())
    shortest, longest = _depth(demo, concept_id)
    body = dict(_concept_ref(demo, concept_id), fsn=graph.name(concept_id), hierarchy=_hierarchy_of(demo, concept_id),
                is_root=concept_id == ROOT_ID,
                parents=sorted((_concept_ref(demo, p) for p in graph.parents_of(concept_id)), key=lambda r: r["name"].lower()),
                children=[_concept_ref(demo, c) for c in children[:CHILD_PAGE]], children_count=len(children),
                ancestors_count=len(graph.ancestors(concept_id)), depth={"shortest": shortest, "longest": longest},
                path=_shortest_path(demo, concept_id))
    body.update(_card_details(demo, concept_id))
    return body


@app.get("/api/concept/{concept_id}/children")
def concept_children(concept_id: str, offset: int = Query(0, ge=0), limit: int = Query(CHILD_PAGE, ge=1, le=500),
                     q: str = Query("", max_length=100)):
    """A page of the concept's children by name; ``q`` keeps those whose name contains it (any case)."""
    demo = state.require()
    if concept_id not in demo.graph:
        raise HTTPException(404, f"{concept_id} is not in the International view")
    children = sorted(demo.graph.children_of(concept_id), key=lambda c: (demo.graph.name(c) or "").lower())
    if q.strip():
        needle = q.strip().lower()
        children = [c for c in children if needle in (demo.graph.name(c) or "").lower() or needle == c]
    return {"total": len(children), "offset": offset,
            "children": [_concept_ref(demo, c) for c in children[offset:offset + limit]]}


@app.get("/api/concept/{concept_id}/lineage")
def lineage(concept_id: str, children: int = Query(LINEAGE_CHILDREN, ge=0, le=50)):
    """Every ancestor of the concept with the IS_A links between them, plus a few children: for the graph view."""
    demo = state.require()
    graph = demo.graph
    if concept_id not in graph:
        raise HTTPException(404, f"{concept_id} is not in the International view")
    up = graph.ancestors(concept_id) | {concept_id}
    edges = [{"child": c, "parent": p} for c in up for p in graph.parents_of(c) if p in up]
    kids = sorted(graph.children_of(concept_id), key=lambda c: (graph.name(c) or "").lower())
    shown = kids[:children]
    edges += [{"child": c, "parent": concept_id} for c in shown]
    nodes = [dict(_concept_ref(demo, n), role="focus" if n == concept_id else "ancestor") for n in sorted(up)]
    nodes += [dict(_concept_ref(demo, c), role="child", leaf=not graph.children_of(c)) for c in shown]
    return {"focus": concept_id, "nodes": nodes, "edges": edges, "children_total": len(kids),
            "children_shown": len(shown)}


class AskRequest(BaseModel):
    task: str = Field(..., pattern="^T[1-6]$")
    x: str = Field(..., min_length=1, max_length=20)
    y: str | None = Field(None, max_length=20)
    system: str = "G-QA"
    withheld: int = Field(0, ge=0, le=75)
    marked: bool = True
    model: str = REFERENCE_MODEL


@app.post("/api/ask")
def ask(req: AskRequest):
    demo = state.require()
    if req.system not in SYSTEMS:
        raise HTTPException(400, f"Unknown system {req.system}")
    if req.withheld not in (0, 25, 50, 75):
        raise HTTPException(400, "Withheld must be 0, 25, 50 or 75")
    return _ask(demo, req)


class CompareRequest(BaseModel):
    task: str = Field(..., pattern="^T[1-6]$")
    x: str = Field(..., min_length=1, max_length=20)
    y: str | None = Field(None, max_length=20)
    model: str = REFERENCE_MODEL


@app.post("/api/compare")
def compare(req: CompareRequest):
    demo = state.require()
    results, skipped = [], []
    for system in COMPARE_SYSTEMS:
        try:
            results.append(_ask(demo, AskRequest(task=req.task, x=req.x, y=req.y, system=system, model=req.model)))
        except HTTPException as exc:
            if exc.status_code != 400 or system != "D-RAG":
                raise
            skipped.append({"system": system, "reason": exc.detail})
    return {"results": results, "skipped": skipped}


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@app.get("/api/results")
def results():
    """Study 2 analysis tables, newest stage first (CSE499, then the pilot)."""
    stages = []
    for name, label in (("v3_cse499", "CSE499 run (preliminary)"), ("v3_pilot", "Pilot (pipeline check only)")):
        folder = RESULTS / name
        summary = folder / "analysis_study2" / "summary.csv"
        responses = folder / "system_responses.jsonl"
        if not responses.exists():
            continue
        n_rows = sum(1 for _ in open(responses, encoding="utf-8"))
        stage = {"id": name, "label": label, "rows": n_rows,
                 "updated": responses.stat().st_mtime, "summary": [], "contrasts": [], "outcomes": [],
                 "analysed_at": summary.stat().st_mtime if summary.exists() else None}
        if summary.exists():
            stage["summary"] = _read_csv(summary)
            for table in ("contrasts", "outcomes"):
                path = folder / "analysis_study2" / f"{table}.csv"
                if path.exists():
                    stage[table] = _read_csv(path)
        stages.append(stage)
    return {"stages": stages}


# ----------------------------------------------------------------------
# the frontend
# ----------------------------------------------------------------------

if (STATIC / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    target = STATIC / path
    if path and target.is_file() and STATIC in target.resolve().parents:
        return FileResponse(target)
    index = STATIC / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "The frontend is not built. See webapp/README.md."}, status_code=404)


def main():
    import argparse
    import webbrowser

    import uvicorn
    parser = argparse.ArgumentParser(description="SNOMED CT Intelligent Explorer web app")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps it on this computer")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
