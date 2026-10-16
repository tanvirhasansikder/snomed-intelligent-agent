# 🧬 SNOMED CT Intelligent Explorer

**LLM-Assisted Intelligent Agent for Interactive Exploration and Explainable Navigation of the SNOMED CT Ontology**

A research project that explores the use of **SNOMED CT, BioPortal, Neo4j, BFS-based graph crawling, and an interactive Streamlit interface** to dynamically retrieve and explore relevant portions of the SNOMED CT clinical terminology hierarchy.

---

## 📌 Project Overview

SNOMED CT is a large clinical terminology containing concepts and relationships organized into a complex hierarchical structure.

The goal of this project is to build an intelligent system that can:

- Resolve a clinical concept from a user's query.
- Retrieve the corresponding SNOMED CT concept.
- Dynamically crawl its relevant hierarchical relationships.
- Store the retrieved concepts and relationships in a Neo4j knowledge graph.
- Allow users to interactively explore the retrieved SNOMED CT hierarchy.
- Provide an interface that can later be extended with an LLM for natural-language explanation and intelligent navigation.

The current implementation focuses primarily on the **SNOMED CT graph scraping and knowledge graph construction pipeline**.

---

## 🎯 Current Objectives

The current system is designed to:

1. Search for SNOMED CT concepts.
2. Retrieve concept information from BioPortal.
3. Retrieve hierarchical parent relationships.
4. Dynamically expand the hierarchy using **Breadth-First Search (BFS)**.
5. Control graph expansion using a configurable depth.
6. Store concepts in Neo4j.
7. Store `IS_A` relationships between concepts.
8. Query the resulting knowledge graph.
9. Provide an interactive Streamlit-based visualization/exploration interface.

---

## 🏗️ System Architecture

```text
                 ┌──────────────────────┐
                 │       User           │
                 │  Clinical Concept    │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │  Query Processor     │
                 │  Concept Resolution  │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │      BioPortal       │
                 │   SNOMED CT API      │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │    BFS Crawler       │
                 │ Hierarchy Expansion  │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │       Neo4j          │
                 │   Knowledge Graph    │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │    Streamlit UI      │
                 │ Interactive Explorer │
                 └──────────────────────┘
```

---

## Feature status

| Feature | Status |
| --- | --- |
| BioPortal client (search, concept, parents, direct children with pagination, ancestors, release version) | Implemented, unit-tested with mocked HTTP; not checked against live BioPortal in the audit |
| Upward BFS crawler with failure recording and reuse of completed parent lists | Implemented, unit-tested |
| Neo4j repository (concepts, IS_A edges, coverage metadata, traversals) | Implemented, unit-tested with a fake database; Cypher passes Neo4j's official linter; not run on a live server in the audit |
| Query processor (ID check-digit validation, exact-label resolution, coverage, typed errors) | Implemented, unit-tested |
| Streamlit UI (`main.py`) | Implemented, tested headlessly with Streamlit's AppTest |
| Deterministic claim verifier and template explanation (`explanation/`) | Implemented, unit-tested |
| Evaluation framework (`evaluation/`) | Implemented, tested on **synthetic** data only; no real benchmark or results yet |
| LLM reasoning / explanations (`llm/`) | Planned, not implemented (files are empty) |
| Visualisation (`visualization/`), RF2 import, `models/`, `query_engine/`, `services/` | Planned placeholders (empty files) |

See `docs/AUDIT.md` for the audit and `docs/CHANGES.md` for what changed.

## Module map

```
main.py (Streamlit)  →  agent/query_processor.py
                           ├─ resolve: local Neo4j, else BioPortal search / concept
                           └─ knowledge_graph/graph_builder.py
                                ├─ crawler/bfs_crawler.py  → parents, grandparents … (upward)
                                ├─ expand_children()       → direct children (one level, paged)
                                └─ database/graph_repository.py → database/neo4j_connection.py
crawler/bioportal_client.py  → https://data.bioontology.org (SNOMEDCT)
ui/view_model.py             → testable presentation logic for main.py
explanation/, evaluation/    → deterministic verifier, metrics, benchmark framework
```

Graph model: `(:Concept {concept_id, name})-[:IS_A]->(:Concept)` (child → parent). Each concept also stores `parents_status` / `children_status` (`complete`, `partial`, `failed`) with retrieval time, source, count and error. **No status means that direction was never retrieved**, so an empty list is unknown, not "none".

## Setup (Windows PowerShell)

```powershell
cd F:\499project\snomedAgent
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env      # then edit .env with your real values
```

`.env` variables (names only; never commit the file):

| Variable | Required | Meaning |
| --- | --- | --- |
| `BIOPORTAL_API_KEY` | Yes | Your BioPortal API key |
| `NEO4J_URI` | Yes | e.g. `bolt://localhost:7687` |
| `NEO4J_USERNAME` | Yes | Neo4j user |
| `NEO4J_PASSWORD` | Yes | Neo4j password |
| `NEO4J_DATABASE` | No | Named database; default database if empty |

Make sure Git ignores `.env`: `git check-ignore -v .env` should print a rule. If it prints nothing, add `.env` to `.gitignore`.

### Neo4j (safe initialisation)

The app only adds nodes and relationships; it never deletes. Optionally add a uniqueness constraint (not destructive). First check that no duplicates exist, in Neo4j Browser:

```cypher
MATCH (c:Concept) WITH c.concept_id AS id, count(*) AS n WHERE n > 1 RETURN id, n;
```

If that returns no rows:

```powershell
python -c "from database.graph_repository import GraphRepository as G; r = G(); r.ensure_constraints(); r.close()"
```

## Run

```powershell
streamlit run main.py
```

## Tests

```powershell
python -m pytest            # unit tests: no network, no database access
python -m pytest -m integration   # optional; needs a DISPOSABLE empty Neo4j database (see tests/test_integration_neo4j.py)
```

Unit tests block real HTTP and real Neo4j drivers and replace credentials with fake values, so they never touch BioPortal or your database.

Optional official Cypher lint (needs Node.js):

```powershell
mkdir $env:TEMP\cypherlint; cd $env:TEMP\cypherlint; npm install @neo4j-cypher/language-support
$env:CYPHER_LINT_NODE_PATH="$env:TEMP\cypherlint\node_modules"; cd F:\499project\snomedAgent; python -m pytest tests/test_cypher_static.py
```

### Small live check (makes about 10 BioPortal requests, no database writes)

```powershell
python -m crawler.bioportal_client
```

## Evaluation (framework only)

```powershell
# ids.txt: one SNOMED CT concept ID per line (max 50 per run)
python -m evaluation.benchmark --ids ids.txt --live --out data\gold\snapshot.json
```

Then use `evaluation.benchmark.generate_items`, `answer_from_graph` and `evaluate`. Protocol, systems and metrics: `docs/EVALUATION_PROTOCOL.md`. Gold snapshots go in `data/gold/`, which Git ignores because they contain SNOMED CT content.

## Data source and licensing

BioPortal's `SNOMEDCT` ontology is the SNOMED CT US Edition distributed through the UMLS; its use is governed by the UMLS licence. Do not publish retrieved SNOMED CT content without confirming it is permitted. Record the BioPortal release version with any results.

## Known limitations

* Only `IS_A` relationships; no attribute relationships.
* Upward expansion is depth-limited; ancestors above the depth limit are reported as a frontier, not shown.
* Direct children are capped (5 pages × 100 by default); larger lists are flagged as partial.
* The crawler does not record the BioPortal release version unless one is passed in.
* Concepts stored by earlier versions have no coverage metadata and are re-fetched once.
* Exact-label matching uses preferred labels only; synonyms are not stored locally.
