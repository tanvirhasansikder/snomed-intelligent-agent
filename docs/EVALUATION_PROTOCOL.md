# Evaluation protocol (draft, not yet executed)

Status: the framework in `evaluation/` and `explanation/` is implemented and unit-tested on a **synthetic** 7-node hierarchy only. No real benchmark has been built, no experiment has been run, and no results exist.

## Research direction

Faithfulness of LLM explanations of SNOMED CT hierarchy over incomplete, on-demand retrieved subgraphs.

| ID | Question |
| --- | --- |
| RQ1 | How often do explanations make unsupported or false hierarchy claims? |
| RQ2 | How does retrieval completeness (direction, depth) change explanation accuracy? |
| RQ3 | Does deterministic verification reduce unsupported claims, and at what cost in completeness? |
| RQ4 | What are the trade-offs between answer quality, retrieval depth, latency and API cost? |

## Ground truth and provenance

1. Sample concept IDs (stratified by top-level hierarchy and depth; sampling script still to be written). Store the ID list in the repository.
2. Build a gold snapshot once: `python -m evaluation.benchmark --ids ids.txt --live` (at most 50 IDs per run unless `allow_large=True` is used in code).
3. The snapshot records: source, ontology, BioPortal release version and date (if BioPortal returns them), retrieval timestamp, per-concept parents, children (with completeness flag), ancestors, and every failure. Concepts with any failed request are excluded, never stored half-complete.
4. All experiments read the frozen snapshot, never live BioPortal.

Schema: `evaluation/benchmark.py` (`GoldSnapshot`, `GoldConcept`, schema version 1.0).

**Licensing:** snapshots contain SNOMED CT content served under the UMLS licence. `data/gold/` is ignored by Git. Publish only concept IDs and scripts unless redistribution is confirmed to be permitted.

## Tasks

| Task | Question | Gold answer |
| --- | --- | --- |
| T1 | Direct parents of X | Set of IDs |
| T2 | Direct children of X | Set of IDs (only when the child list is complete) |
| T3 | Is X a kind of Y? | Boolean; positives from X's ancestors, negatives currently random non-ancestors |
| T6 (planned) | Free-text explanation of X's position | Claims scored by the verifier |

Hard negatives (siblings, cousins) and path tasks (T4/T5 in the research plan) are not implemented yet.

## Systems

| ID | System | Status |
| --- | --- | --- |
| R0 | Deterministic answers / template explanation from the local graph (`answer_from_graph`, `explain_position`) | Implemented |
| B0 | LLM, closed book | Planned (`llm/` is empty) |
| B1 | LLM + current upward-only subgraph | Planned |
| B2 | LLM writing Cypher over Neo4j | Planned |
| S1 | LLM + question-aware retrieval citing edge IDs | Planned |
| S2 | S1 + claim verifier (`filter_supported`) | Verifier implemented; LLM part planned |

## Metrics (`evaluation/metrics.py`)

| Metric | Definition |
| --- | --- |
| Set precision / recall / F1 | Per item, macro-averaged; empty-set conventions in the module docstring |
| Accuracy | T3 |
| Path edge-F1, exact match | For path tasks |
| Claim label rates | Share of claims per verifier label |
| Unsupported-claim rate | 1 − share labelled `supported_by_subgraph` |
| False-claim rate | Share labelled `false_in_reference` or `unknown_identifier` |
| ID hallucination rate | Share of cited concept IDs absent from the reference |
| Citation precision / recall | Cited edges that exist in the evidence / required edges that were cited |
| Cost | BioPortal requests (`request_count`, crawl report), wall-clock latency, LLM tokens (planned) |
| Uncertainty | Percentile bootstrap 95% CI, fixed seed |

## Verifier labels (`explanation/explainer.py`)

`supported_by_subgraph`, `true_not_retrieved`, `false_in_reference`, `contradicted_by_complete_evidence`, `insufficient_evidence`, `unknown_identifier`. Without a reference, a claim is called false only when the relevant evidence is known to be complete.

## Procedure

1. Freeze ID list, gold snapshot, prompts, model IDs and parameters in the repository before the main run.
2. Pilot on a small sample to debug; pilot numbers are reported separately and never mixed with final results.
3. Main run with fixed seeds (item generation, bootstrap); repeat LLM runs to measure nondeterminism; log raw outputs.
4. Paired statistics across systems (McNemar for accuracy, Wilcoxon for per-item F1), Holm correction.
5. Error analysis on stratified failures using the verifier labels.

## Threats to validity

* BioPortal serves the US Edition via UMLS; results may not transfer to other editions.
* Gold answers inherit any BioPortal conversion artefacts.
* Template questions differ from real user questions.
* Random negatives make T3 easier than hard negatives would.
* Only `IS_A` relations are covered.
