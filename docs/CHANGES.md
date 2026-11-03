# Change log — audit and repair (2026-10-17)

Commits (local working copy; apply via the patch):

1. `ee43bb5` Baseline: verbatim rebuild of the Project snapshot.
2. `2acbd30` Test suite added; baseline run 56 passed, 125 failed, 1 collection error.
3. `ee03c27` Core pipeline and UI repairs.
4. `f6ae36d` Static Cypher test ignores docstrings.
5. `d59fad2` Claim verifier, template explanation, evaluation framework, fixtures.
6. Pagination robustness; documentation (this file, README, AUDIT, protocol).

## Behaviour you will notice

| Change | Why | How to get the old behaviour |
| --- | --- | --- |
| Direct children are retrieved from BioPortal for each query (sidebar toggle, on by default) | Children were never retrieved before | Untick "Retrieve direct children from BioPortal" |
| Repeat queries do not call BioPortal again for concepts whose parent list was retrieved completely | Avoid repeated API load and latency | `BFSCrawler(use_cache=False)` or `GraphBuilder(use_cache=False)` |
| Empty parent/child lists come with a message saying whether they are confirmed or unknown | An unretrieved list was shown as "none" | — |
| Third statistic card shows "Ancestors Retrieved" instead of parents + children | The old sum mixed complete and unknown counts | — |
| Failed queries show a specific error and no result card | A failed query used to render "Unknown" with a success banner | — |
| IDs with a wrong check digit are rejected before any API call | Avoid wasted calls and misleading "not found" | — |
| Name queries prefer an exact label match among BioPortal results | `results[0]` is not always the exact concept | — |
| Console `print` output replaced by `logging` | Quieter Streamlit console; levels configurable | `logging.basicConfig(level=logging.INFO)` |
| `GraphRepository.clear_database()` requires `confirm=True` | It deletes everything | — |
| `BioPortalClient.get_children()` returns direct children (`/children`), not all descendants | The method name and docstring said children | — |

## Files

| File | Change | Reason |
| --- | --- | --- |
| `config/settings.py` | Loads `.env` from project root; `get_setting()`; `DATA_SOURCE` label | Single, working-directory-independent configuration (M2, L3) |
| `crawler/bioportal_client.py` | Typed errors, `raise_errors`, bounded retries with backoff, `/children` with pagination, `fetch_children`, `get_ancestors`, `get_ontology_version`, request counter, secret-free messages | C1, C2, H4, H5 |
| `crawler/bfs_crawler.py` | Injected client/repository, `CrawlReport`, failure recording, malformed/self-loop filtering, cache of completed parent lists, label reuse, logging | C1, C3, H2, H6 |
| `database/neo4j_connection.py` | Named configuration errors, shared lazy driver, optional `NEO4J_DATABASE`, hidden password in `repr` | M2, M3 |
| `database/graph_repository.py` | Literal validated traversal bounds, `save_relationship` returns real result, batched `save_children`, coverage properties, `find_upward_path`, `ensure_constraints`, guarded `clear_database` | H1, H3, C3, M6, M8, L2 |
| `knowledge_graph/graph_builder.py` | Shares repository/client, `expand_children`, honest return value, reads delegate to repository | C2, M3, M5 |
| `agent/query_processor.py` | Input and SCTID validation, exact-label resolution, typed error results, coverage, warnings, ancestors; original keys and methods kept | C2, M1, M7 |
| `main.py` | Imports helpers from `ui/view_model.py`; children toggle; coverage notices; escaped labels; error handling; status caption | M4 |
| `ui/view_model.py` (new) | Helpers moved from `main.py` unchanged, plus coverage and error messages | Testable UI logic |
| `ui/streamlit_app.py` | Docstring only | States it is not an entry point |
| `explanation/explainer.py` (new) | Deterministic claim verifier with six labels | Foundation for faithfulness evaluation |
| `explanation/path_explainer.py` (new) | Template explanation with evidence edges (R0) | Deterministic baseline |
| `evaluation/metrics.py` (new) | P/R/F1, accuracy, path edge-F1, claim rates, ID hallucination, citation P/R, bootstrap CI | Evaluation framework |
| `evaluation/benchmark.py` (new) | Gold-snapshot schema with provenance, bounded builder, items, R0 answers, scoring, guarded CLI | Evaluation framework |
| `tests/` (new) | 256 unit tests + 1 optional integration test | Phase C |
| `pytest.ini`, `requirements-dev.txt` (new) | Test configuration and dev dependency | L5 |
| `README.md`, `.env.example`, `docs/*` | Documentation | Phase H |
| `data/gold/.gitignore` (new) | Keeps gold snapshots (SNOMED CT content) out of Git | Licensing |

Not changed: `requirements.txt`, `crawler/cache.py`, `crawler/queue_manager.py`, `crawler/state_manager.py`, all other empty placeholder modules, `llm/`.


# Research pipeline (2026-11-02)

New, no change to app behaviour except where noted:

| File | Purpose |
| --- | --- |
| `docs/RESEARCH_PROTOCOL.md`, `docs/LITERATURE_SEARCH.md`, `docs/JOURNALS.md`, `docs/RUN_GUIDE.md` | Frozen protocol v1.0, search log and novelty assessment, journal comparison, run guide |
| `database/memory_repository.py` | In-memory GraphRepository for experiments (no Neo4j needed) |
| `evaluation/bioportal_cache.py`, `collect.py`, `synthetic.py`, `sctid.py` | Record/replay of BioPortal, sampling and closure collection, synthetic test ontology, SCTID helpers |
| `evaluation/gold.py`, `tasks.py`, `conditions.py`, `scoring.py`, `stats.py`, `runner.py`, `report.py`, `annotation.py` | Gold index, items and sufficiency, retrieval conditions via the real crawler, scoring, statistics, runner, analysis, human annotation |
| `llm/client.py`, `providers.py`, `prompt_manager.py`, `reasoner.py` | Provider-neutral LLM client with cache, presets, versioned prompts, answer parser (previously empty files) |
| `explanation/explainer.py` | Adds absence claims (`no_children`, `no_parents`) and child completeness; existing behaviour unchanged |
| `configs/*.json` | Dry-run, pilot and main configurations |
| `paper/` | Manuscript draft (methods written; results pending) and verified bibliography |
| `tests/test_research_pipeline.py` | 36 tests for the above |
| `requirements-research.txt` | Optional: matplotlib for figures |
| `.gitignore` | Ignores `data/` caches, `results/` and LaTeX build files (SNOMED CT content must not be committed) |

## 2026-11-03: protocol v1.1 (Amendment 1)

These changes follow the author's decisions: supervisor approval, a US$20–30 budget for one month of API access, no clinician annotator, and no UMLS licence.

| Change | Why |
| --- | --- |
| `llm/client.py`: optional `extra_body` (e.g. OpenRouter provider pinning), included in the cache fingerprint only when set; records `served_by` and the provider-reported `cost_usd` | Reproducibility on a router; real cost tracking |
| `evaluation/runner.py`: `--max-cost` guard, per-call `cost_usd` and `served_by`, cost estimate in `--dry-run` from `price_per_mtok` | Hard budget limit |
| `evaluation/report.py`: `cost_usd` column in `summary.csv` | Report spend per cell |
| `configs/pilot.json`, `configs/main.json`: three pinned OpenRouter models with prices and budgets | The Groq Llama models became enterprise-only |
| `evaluation/annotation.py`: annotators blind to model, condition and format; "clinically misleading" question replaced by "contradicts the structured claims" | No clinical annotator; avoid unblinded judgements |
| `docs/LICENSING.md` (new), `docs/ANNOTATION_GUIDE.md` (new), protocol Amendment 1, `RUN_GUIDE.md`, `JOURNALS.md`, `README.md`, `paper/main.tex`, `.env.example` | Licensing route, the new procedure, journal re-ranking |
| Tests: 4 new (pinning, cost, budget stop, blind annotation export) | |
