# Code audit — SNOMED CT Intelligent Explorer

Audit date: 2026-10-17. Scope: the 42-file snapshot of `snomedAgent` uploaded to the claude.ai Project on 2026-10-16. The live folder `F:\499project\snomedAgent` (and its Git history) was **not** accessible, so differences between the snapshot and your machine are possible (see "Open questions").

## Summary

The original BFS logic is correct: depth limits, polyhierarchy (two parents) and cycles all behaved correctly under test. The defects were in the layers around it. Failures were silently turned into "no parents". Children were never retrieved but were shown as if none existed. Two Cypher queries were invalid. Nothing recorded what had or had not been retrieved. All of these are fixed and covered by regression tests.

## Baseline (before any change)

| Check | Command | Result |
| --- | --- | --- |
| Environment | `python3.13 -m venv .venv`, `pip install -r requirements.txt` | Python 3.13.16; all pinned packages installed (neo4j 6.2.0, streamlit 1.60.0, requests 2.34.2) |
| Version control | `git init` on the rebuilt snapshot | Baseline commit `ee43bb5` (local only; your real commit hash unknown) |
| Compilation | `python -m compileall -q -x '\.venv' .` | Pass |
| Imports | `import` of all 10 non-empty modules | Pass |
| Construction without credentials | `BioPortalClient()`, `Neo4jConnection()`, `QueryProcessor()` | ValueError (key named); Neo4j: `URI scheme b'' is not supported` (variable not named) |
| Streamlit startup | `AppTest.from_file("main.py").run()` | Starts; a query without credentials shows a raw driver traceback |
| Cypher | 19 static queries linted with `@neo4j-cypher/language-support` 2.0.0-next.6 | 17 pass, **2 errors** (`graph_repository.py` lines 199, 225) |
| Existing tests | `tests/` | None in the snapshot |
| New suite on original code | `python -m pytest` | **56 passed, 125 failed, 1 collection error** (`docs/baseline_test_results.txt`) |

Many baseline failures in the builder, connection and query-processor tests occur because the original constructors could not accept test doubles. The client, crawler and repository failures are behavioural defects listed below.

## Findings (severity-ranked)

Status key: **Fixed** = changed and covered by a regression test that failed on the original. **Guarded** = safety restriction added. **Documented** = left as is, explained.

### Critical — produces wrong research data

| ID | Finding | Evidence (original) | Status |
| --- | --- | --- | --- |
| C1 | API failures indistinguishable from empty results | `get_parents` returned `[]` on HTTP 5xx, timeouts and exceptions; the crawler then treated the concept as having no parents | Fixed: typed `BioPortalRequestError`, `raise_errors=True` in the pipeline, failures recorded on the node and in the crawl report |
| C2 | Children never retrieved, yet empty lists shown as "No direct children" | Crawler called only `get_parents`; `get_children` hit `/descendants` (all levels, first page) and was unused | Fixed: `/children` with pagination and truncation flag; children fetched per query (UI toggle); coverage-aware messages |
| C3 | No record of what was retrieved, when, or from where | Nodes stored only `concept_id` and `name` | Fixed: `parents_*` / `children_*` status, timestamp, source, count, error on each node. Release version is recorded only if supplied (see R2) |

### High

| ID | Finding | Evidence | Status |
| --- | --- | --- | --- |
| H1 | Invalid Cypher: parameter as variable-length bound | `[:IS_A*1..$max_depth]` in `get_ancestors`, `get_descendants`; rejected by Neo4j's linter | Fixed: validated integer 1–20 written into the query; all rendered queries pass the linter |
| H2 | Self-loops and non-numeric IDs saved as IS_A edges | A parent record equal to the child produced `(700)-[:IS_A]->(700)` | Fixed: skipped and counted as malformed |
| H3 | `clear_database()` deleted every node on a bare call | No confirmation | Guarded: requires `confirm=True` |
| H4 | A non-JSON 200 response crashed the app | `response.json()` ValueError escaped the `RequestException` handler | Fixed |
| H5 | No retry or backoff; `tenacity` installed but unused | Single attempt, 30 s timeout | Fixed: up to 2 retries for timeouts, connection errors, 429 and 5xx; exponential backoff capped at 10 s; honours `Retry-After`; 4xx not retried (`tenacity` still unused) |
| H6 | Every query re-crawled BioPortal, with duplicate requests | `expand_concept` crawled even when data existed; each node re-fetched by `get_concept` after its label was already known | Fixed: completed parent lists are reused; labels reused; `use_cache=False` forces refresh |

### Medium

| ID | Finding | Status |
| --- | --- | --- |
| M1 | Name queries took BioPortal `results[0]` with no ambiguity signal | Fixed: exact label preferred, else top result flagged ambiguous, candidates returned |
| M2 | Missing `NEO4J_URI` gave `URI scheme b'' is not supported`; `.env` loaded relative to the working directory | Fixed: error names the missing variable (never its value); `.env` loaded from project root |
| M3 | Up to three Neo4j drivers created per query, never closed | Fixed: one shared, lazily created driver |
| M4 | UI: success banner even on failure; failed queries rendered an "Unknown" concept; "LLM-Assisted" / "Explainable Result" with no LLM; labels inserted unescaped into HTML | Fixed: errors per type, caption stating LLM explanations are not implemented, HTML escaping |
| M5 | `GraphBuilder.get_concept` returned `[{concept_id: None, name: None}]` for a concept with no parents | Fixed |
| M6 | `save_relationship` returned True even when an endpoint was missing | Fixed |
| M7 | No input validation; SNOMED CT IDs not check-digit validated | Fixed: length limit, Verhoeff check digit, concept partition |
| M8 | `find_shortest_path` is undirected (can go up then down) | Documented; added directed `find_upward_path` |

### Low

| ID | Finding | Status |
| --- | --- | --- |
| L1 | `print` used for all diagnostics | Replaced with `logging` in changed modules |
| L2 | No uniqueness constraint on `Concept.concept_id` | `ensure_constraints()` added; not run automatically |
| L3 | `config/settings.py` not used by any module | Now the single place `.env` is loaded |
| L4 | README was one line; no setup or `.env` documentation | Rewritten; `.env.example` added |
| L5 | `pytest` not declared | `requirements-dev.txt` added; `requirements.txt` unchanged |
| L6 | `crawler/cache.py`, `queue_manager.py`, `state_manager.py` never imported | Documented, left untouched |
| L7 | 22 empty placeholder modules | Documented, left untouched (`ui/streamlit_app.py` got a docstring saying it is not an entry point) |

## Authoritative entry point

`main.py` is the only Streamlit entry point (`streamlit run main.py`). `ui/streamlit_app.py` was empty in the snapshot and is not used.

## Remaining risks

| ID | Risk |
| --- | --- |
| R1 | No live BioPortal call was made. The format of `/children` pagination, `/ancestors` and `latest_submission` follows the documentation where it is explicit and is otherwise handled defensively; verify with the bounded live check in the README. |
| R2 | The crawler does not fetch the BioPortal release version on its own (`source_version` stays `None` unless passed in). The gold-snapshot builder does record it. |
| R3 | No query was executed on a real Neo4j server. Syntax was checked with Neo4j's official linter; behaviour on a server is covered only by the optional integration test, which has not been run. |
| R4 | Concepts stored by the original crawler have no coverage properties, so they are re-fetched once on first use. |
| R5 | Only `IS_A` is modelled; attribute relationships (finding site, causative agent, etc.) are out of scope. |

## Open questions for you

1. Your project instructions mention `explaination/`, `tests/` and `crawler_state/`; the snapshot has `explanation/` and neither of the others. Please confirm what is on disk.
2. Is your local code newer than the 2026-10-16 upload? If so, apply the patch on a branch and resolve conflicts before trusting the tests.
3. Do you hold a UMLS licence covering your BioPortal SNOMEDCT use? This matters before any data is published.
