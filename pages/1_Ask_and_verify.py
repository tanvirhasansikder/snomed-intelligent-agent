"""
Ask and verify: one SNOMED CT hierarchy question through the Study 2 pipeline.

Run the app as usual (``streamlit run main.py``); this page appears in the
sidebar. It reads the local snomed.db (International view), shows the
evidence each retrieval system gives the model, the model's answer, and how
every claim checks out against the release. Logic: ui/ask_verify.py.
"""

import json
import os
from pathlib import Path

import streamlit as st

import config.settings  # noqa: F401  (loads .env: GEMINI_API_KEY)
from ui.ask_verify import (
    NEEDS_TARGET, SYSTEM_HELP, SYSTEMS, TASK_TEXT, AskVerify, DemoError, ReadThroughCache, describe_label,
    make_item,
)

ROOT = Path(__file__).resolve().parents[1]
#: SNOMED_DB, SNOMED_DENSE_DIR and SNOMED_DEMO_CONFIG override the default locations (e.g. for tests)
DB = Path(os.environ.get("SNOMED_DB", ROOT / "data" / "snomed_db" / "snomed.db"))
DENSE = Path(os.environ.get("SNOMED_DENSE_DIR", ROOT / "data" / "snomed_db" / "dense"))
CONFIG = Path(os.environ.get("SNOMED_DEMO_CONFIG", ROOT / "configs" / "v3_cse499.json"))
STUDY_CACHE = ROOT / "results" / "llm_cache_v3.jsonl"
DEMO_CACHE = Path(os.environ.get("SNOMED_DEMO_CACHE", ROOT / "results" / "llm_cache_demo.jsonl"))

st.set_page_config(page_title="Ask and verify", page_icon="🔎", layout="wide")


# ----------------------------------------------------------------------
# resources (loaded once per server)
# ----------------------------------------------------------------------

@st.cache_resource(show_spinner="Loading the International view of SNOMED CT (about 40 s, once)...")
def load_demo():
    from crawler.local_client import LocalTerminologyClient
    vectors = {}
    for path in sorted(DENSE.glob("query_vectors_*.npz")) if DENSE.exists() else []:
        from evaluation.dense_retrieval import load_query_vectors
        vectors.update(load_query_vectors(path))

    def encoder():
        from evaluation.dense_retrieval import load_encoder
        return load_encoder()

    client = LocalTerminologyClient.from_db(DB)
    return AskVerify(client, DB, dense_dir=DENSE if (DENSE / "ids.json").exists() else None,
                     query_vectors=vectors, encoder_loader=encoder)


@st.cache_resource
def load_llm(model_name):
    from llm.client import CachedClient
    from llm.providers import build_client
    spec = next(m for m in json.loads(CONFIG.read_text(encoding="utf-8"))["models"] if m["name"] == model_name)
    return CachedClient(build_client(spec), ReadThroughCache(DEMO_CACHE, STUDY_CACHE if STUDY_CACHE.exists() else None))


def model_names():
    return [m["name"] for m in json.loads(CONFIG.read_text(encoding="utf-8"))["models"]]


# ----------------------------------------------------------------------
# page
# ----------------------------------------------------------------------

st.title("🔎 Ask and verify")
st.caption("SNOMED CT International Edition 2026-02-01, International view (351,751 concepts). "
           "Each answer is checked claim by claim against the release.")

if not DB.exists():
    st.error(f"The database is not at {DB}. Put snomed.db there and reload.")
    st.stop()

demo = load_demo()

with st.sidebar:
    st.header("Settings")
    model = st.selectbox("Model", model_names(), help="Answers already in the study's cache are replayed for free.")
    st.caption("A new question costs 1 call (2 if verification regenerates) from the provider's free quota.")
    st.divider()
    st.markdown("**Claim labels**")
    for label in ("supported_by_subgraph", "true_not_retrieved", "false_in_reference", "unknown_identifier"):
        short, long = describe_label(label)
        st.markdown(f"- **{short}**: {long}")


def concept_picker(title, key):
    query = st.text_input(title, key=f"{key}_q", placeholder="e.g. myocardial infarction, or a concept ID")
    if not query:
        return None
    query = query.strip()
    if query.isdigit():
        if query in demo.graph:
            st.caption(f"{demo.label(query)} [{query}]")
            return query
        st.warning("That ID is not in the International view.")
        return None
    hits = demo.local.search_concept(query, pagesize=10)
    if not hits:
        st.warning("No concept found.")
        return None
    options = {f"{h['name']} [{h['concept_id']}]": h["concept_id"] for h in hits}
    return options[st.selectbox("Matches", list(options), key=f"{key}_pick")]


left, right = st.columns([1, 1])
with left:
    task = st.selectbox("Question type", list(TASK_TEXT), format_func=lambda t: f"{t}: {TASK_TEXT[t]}")
    x = concept_picker("Concept X", "x")
with right:
    y = concept_picker("Concept Y", "y") if task in NEEDS_TARGET else None
    system = st.radio("Retrieval system", list(SYSTEMS), horizontal=True, help="\n\n".join(
        f"**{k}**: {v}" for k, v in SYSTEM_HELP.items()))
    st.caption(SYSTEM_HELP[system])
    fraction, marked = 0.0, True
    if system in ("G-QA", "G-QA+V"):
        fraction = st.select_slider("Withhold needed links", options=[0, 25, 50, 75], value=0,
                                    format_func=lambda v: f"{v}%") / 100
        marked = st.checkbox("Show coverage marks (COMPLETE / PARTIAL)", value=True)

ask, compare = st.columns(2)
run_one = ask.button("Ask", type="primary", disabled=x is None)
run_all = compare.button("Compare all systems", disabled=x is None,
                         help="Asks the same question with B0, L-RAG, D-RAG, G-QA and G-QA+V (up to 6 calls).")


def concept(cid):
    return f"{demo.label(cid)} [{cid}]"


def show_answer(answer):
    if isinstance(answer, list):
        return ", ".join(concept(a) for a in answer) if answer else "(empty list)"
    return str(answer)


def outcome(result):
    if result.error:
        return "error"
    if result.abstained:
        return "abstained"
    score = result.score.get("score")
    return "correct" if score is not None and score >= 0.999 else ("partly correct" if score else "wrong")


def claims_table(claims):
    rows = []
    for c in claims:
        short, long = describe_label(c["label"])
        rows.append({"claim": f"{concept(c['subject'])} {c['relation']}"
                              + (f" {concept(c['object'])}" if c.get("object") else ""),
                     "source": c.get("source"), "cites": ", ".join(c.get("cites") or []),
                     "check": short, "citation": c.get("citation")})
    return rows


def show_result(result):
    st.subheader(f"{result.system}" + (f" · {int(result.fraction * 100)}% withheld" if result.fraction else "")
                 + ("" if result.marked or result.system not in ("G-QA", "G-QA+V") else " · no coverage marks"))
    if result.error:
        st.error(result.error)
        return
    a, b, c, d = st.columns(4)
    a.metric("Outcome", outcome(result))
    b.metric("Evidence sufficient", "yes" if result.sufficient else "no")
    unsupported = sum(1 for cl in result.claims if cl["label"] != "supported_by_subgraph")
    c.metric("Claims not in evidence", f"{unsupported} of {len(result.claims)}")
    d.metric("From cache", "yes" if result.cached else "no")
    st.markdown(f"**Answer:** {show_answer(result.answer)}")
    if result.item.gold is not None and result.item.task != "T5":
        st.markdown(f"**SNOMED CT says:** {show_answer(result.item.gold)}")
    if result.claims:
        st.dataframe(claims_table(result.claims), use_container_width=True, hide_index=True)
    if result.system == "G-QA+V":
        if result.regenerated or result.removed_claims:
            with st.expander(f"Verification: regenerated = {result.regenerated}, "
                             f"claims removed = {len(result.removed_claims)}"):
                st.markdown(f"**First answer:** {show_answer(result.first_answer)}")
                st.dataframe(claims_table(result.first_claims), use_container_width=True, hide_index=True)
        else:
            st.caption("Verification: every claim was supported; nothing changed.")
    with st.expander("Evidence shown to the model"):
        if result.mask:
            st.caption(f"Withheld {result.mask['n_withheld']} of {result.mask['n_relevant']} needed links.")
        if result.text_record:
            st.caption("Search queries: " + " | ".join(result.text_record["queries"]))
        st.code(result.evidence_text, language=None)
    if result.tokens:
        st.caption(f"Tokens in/out: {result.tokens[0]}/{result.tokens[1]} · model time "
                   f"{(result.latency_s or 0):.1f} s")


if run_one or run_all:
    try:
        item = make_item(demo.graph, task, x, y)
    except DemoError as exc:
        st.warning(str(exc))
        st.stop()
    llm = load_llm(model)
    plan = [(system, fraction, marked)] if run_one else [(s, 0.0, True) for s in SYSTEMS]
    results = []
    for name, f, m in plan:
        with st.spinner(f"{name}: retrieving and asking the model..."):
            try:
                results.append(demo.ask(item, name, llm, fraction=f, marked=m))
            except DemoError as exc:
                st.warning(f"{name}: {exc}")
    if run_all and results:
        st.subheader("Summary")
        st.dataframe([{"system": r.system, "outcome": outcome(r), "evidence sufficient": r.sufficient,
                       "claims": len(r.claims),
                       "not in evidence": sum(1 for cl in r.claims if cl["label"] != "supported_by_subgraph"),
                       "false": sum(1 for cl in r.claims if cl["label"] == "false_in_reference"),
                       "evidence chars": len(r.evidence_text)} for r in results],
                     use_container_width=True, hide_index=True)
    for result in results:
        st.divider()
        show_result(result)
