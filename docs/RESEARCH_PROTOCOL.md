# Research protocol v1.1 (frozen before data collection)

v1.0 was written on 2026-11-02; v1.1 (2026-11-03) applies Amendment 1 below. Both versions precede any data collection or model run.

Working title: **Coverage-aware grounding: measuring parametric fill-in when large language models explain SNOMED CT hierarchy from partial, on-demand retrieved subgraphs.**

Status: design frozen on 2026-11-02, before any real data was collected. Changes after the pilot must be logged in the "Amendments" section with a date and reason. Pilot results are never reported as findings.

## 1. Problem

Our system retrieves SNOMED CT on demand from BioPortal, so the graph an LLM sees is usually incomplete: ancestors stop at a depth limit, and children may not be retrieved at all. A model can then (a) answer only from evidence, (b) abstain, (c) fill gaps from its own memory, correctly or not, or (d) state false absences ("X has no subtypes"). Prior work shows KG-RAG degrades under incompleteness and that models rarely abstain when context is insufficient (see `docs/LITERATURE_SEARCH.md`). It has not been measured for clinical terminology hierarchies, nor whether telling the model *which parts of the evidence are complete* changes its behaviour.

## 2. Research questions and hypotheses

| ID | Question | Hypothesis (directional, tested) |
| --- | --- | --- |
| RQ1 | Without retrieval, how often do LLMs make false SNOMED CT hierarchy claims, and does this depend on concept depth? | H1: closed-book false-claim rate increases with concept depth (trend test). |
| RQ2 | How does retrieval completeness change answer correctness and the source of claims? | H2a: correctness increases from none → up-1 → up-2 → up-2+children → full. H2b: under incomplete retrieval, a substantial share of correct claims are *not* supported by the evidence (parametric fill-in). |
| RQ3 | Does coverage-annotated context (complete / truncated / not retrieved) change behaviour under incomplete retrieval? | H3a: fewer false absence claims (e.g. "no children" when children were simply not retrieved). H3b: fewer evidence-misattributed claims (claimed as from evidence but not in it). H3c: more abstention on insufficient items, without lower correctness on sufficient items. |
| RQ4 | What does deterministic claim verification cost? | Descriptive: share of claims removed, split into false vs true-but-not-retrieved; resulting precision/recall trade-off. (Not a hypothesis: removal of unsupported claims is guaranteed by construction.) |

Primary hypotheses: H2a, H3a, H3b. Others are secondary. Holm correction is applied within each family.

## 3. Data

* **Source:** BioPortal `SNOMEDCT` (US Edition via UMLS). Release recorded from `latest_submission` (2025_09_01 as of 2026-10-17). All raw API responses are cached on disk with timestamps.
* **Sampling:** for each of 6 top-level hierarchies (Clinical finding 404684003, Procedure 71388002, Body structure 123037004, Substance 105590001, Pharmaceutical/biologic product 373873005, Organism 410607006), seeded random descent from the top-level concept through direct children to a target depth drawn uniformly from 2–8 (stopping early at leaves). Duplicates are re-drawn. Top-level IDs are validated as children of the root (138875005) at run time.
* **Size:** main study 30 concepts per hierarchy (180). Pilot: 5 per hierarchy (30) with a different seed, excluded from the main set.
* **Gold closure per concept:** label, direct parents, direct children (with completeness flag), full ancestor closure with the direct parents of every ancestor, children of each direct parent (for sibling negatives). Shortest and longest path to the root are computed from the closure.
* **Known bias:** random descent over-samples concepts below nodes with few children. Reported as a limitation; depth and hierarchy are reported for every result.
* **Licensing (prerequisite):** Bangladesh is not a SNOMED International Member territory. Live collection starts only after the author holds a SNOMED CT licence that covers this research use: a SNOMED International Affiliate licence (registered through MLDS) with a research fee waiver, see `docs/LICENSING.md`. BioPortal's terms restrict users without a licence to exploring and evaluating SNOMED CT, and forbid copying identifiers into a database or document. Snapshots stay in `data/` (Git-ignored) and are never redistributed. We release code, configuration and seeds, from which a licensee can regenerate the data set; whether the list of sampled concept identifiers may be published is confirmed with SNOMED International first. Prompts sent to external LLM providers contain SNOMED CT labels; we request providers that do not retain data, and disclose this.

## 4. Tasks (automatic gold)

| Task | Question | Gold | Primary score |
| --- | --- | --- | --- |
| T1 | Direct parents of X | set of IDs | set F1 |
| T2 | Direct children of X (only when the gold child list is complete and has ≤ 100 children) | set of IDs, possibly empty | set F1; "no children" claims scored separately |
| T3+ | Is X a kind of Y? Y = ancestor at ≥ 2 IS_A steps (root excluded) | yes | accuracy |
| T3− | Is X a kind of Y? Y = sibling of X (child of a parent of X, not an ancestor of X) | no | accuracy |
| T4 | Give an IS_A chain from X to its top-level concept | any valid chain | edge precision, reaches target |
| T5 | Explain where X sits in the hierarchy | structured claims + text | claim labels (Section 7) |

Each answer is JSON with concept IDs, each claim tagged `source: evidence | model_knowledge`, plus an `insufficient_evidence` flag.

## 5. Retrieval conditions (real crawler, offline)

Contexts are produced by the project's own `BFSCrawler` / `GraphBuilder` running against a `SnapshotClient` that replays the frozen BioPortal responses. Results therefore describe the deployed retrieval code, and runs are deterministic.

| Code | Retrieval |
| --- | --- |
| C0 | none (closed book) |
| U1 | upward BFS, depth 1 |
| U2 | upward BFS, depth 2 (current app default, children off) |
| U2D | upward depth 2 + direct children of X (current app with children on) |
| FULL | full ancestor closure + direct children of X |

**Context format factor A** (all conditions except C0):
* `plain`: IS_A edges only.
* `annotated`: the same edges plus, per concept, whether its parent list and child list are complete, truncated, failed or not retrieved, and which concepts lie at the depth frontier.

**Sufficiency** of each item × condition is computed deterministically (e.g. T1 is sufficient iff X's parent list is complete in the context; T3− is sufficient only if X's full ancestor closure is present).

## 6. Systems

| Code | System |
| --- | --- |
| R0 | Deterministic template answer from the context (no LLM): evidence-only reference |
| L | LLM answer (per model × condition × format) |
| LV | L after deterministic verification: claims not supported by the context are removed; answers with no remaining support become "insufficient evidence" |

Text-to-Cypher (as in Soman et al. 2024) is excluded: it needs a live database per condition and is a different research question. Stated as a limitation.

**Models (Amendment 1):** three models through OpenRouter, each pinned to one upstream provider with fallbacks disabled: Llama 3.1 8B Instruct and Llama 3.3 70B Instruct (open weights, two sizes of one family) and GPT-4o mini, dated snapshot 2024-07-18 (closed, widely used). Exact model identifiers, the provider that served each call, date, temperature 0, max tokens and cost are recorded per call. Main run: one response per item. Nondeterminism: 3 repetitions on a random 10% subset.

**Budget:** US$20–30 in total for one month of API access. The runner's `--max-cost` guard stops a run at a set spend. The dry-run estimate on synthetic data at main-study size is about US$5 for the three models (worst case about US$10 if every answer used the 800-token limit); real labels are longer, so we plan for about twice that. The pilot is capped at US$3 and the main study at US$20. If the money runs out, the smallest model is dropped first, and the reason is recorded here.

## 7. Claim labels and metrics

Claims are typed triples: `direct_parent(X,Y)`, `ancestor(X,Y)`, `no_children(X)`, `no_parents(X)`. Each is labelled by `explanation/explainer.py` against the context and the frozen reference:

`supported_by_subgraph` · `true_not_retrieved` (parametric fill-in, correct) · `false_in_reference` · `unknown_identifier` · `contradicted_by_complete_evidence` · `insufficient_evidence`

Metrics: set P/R/F1 (T1, T2), accuracy (T3), edge precision and target reached (T4), unsupported-claim rate, false-claim rate, ID hallucination rate, **misattribution rate** (claim tagged `evidence` but not supported), **false-absence rate** (`no_children`/`no_parents` claims that are false), abstention rate on sufficient vs insufficient items, tokens, latency.

## 8. Analysis

* Paired comparisons on identical items: exact McNemar (binary), Wilcoxon signed-rank (per-item F1), Holm correction within each hypothesis family.
* Trend over depth (H1): Cochran–Armitage trend test on false-claim presence by depth bin.
* 95% percentile bootstrap CIs (1,000 resamples, seed 0) for every reported rate.
* All results reported per model, overall and by hierarchy and depth; no pooling across models for hypothesis tests.
* Effect sizes reported alongside p-values. No result is dropped; failed or unparseable responses are counted as such.

## 9. Human evaluation

Two annotators, independently, on 100 randomly sampled T5 explanations (stratified by model, condition and format; annotators blind to all three): (a) does the free text state any hierarchical claim not in the structured claim list? (b) does the free text contradict the structured claims or the answer? Cohen's kappa reported; disagreements adjudicated. This validates that structured-claim scoring reflects the text.

Both questions compare a model's text with its own structured output and need no clinical expertise; the annotators are the author and one trained peer (computer science), working from a written guideline. Correctness of every hierarchical claim is judged automatically against the frozen SNOMED CT hierarchy, not by humans. Clinical appropriateness and usefulness are **out of scope** (Amendment 1): the paper makes no claim about clinical safety or utility.

## 10. Procedure

0. Obtain the SNOMED CT licence (Section 3). Until then, only the synthetic dry run is performed.
1. Freeze this protocol and `configs/*.json` in Git (tag `protocol-v1.1`).
2. Pilot (30 concepts, the three study models, US$3 cap): check parsing, cost, timing; fix bugs only. Pilot outputs stay in `results/pilot/`.
3. Record amendments.
4. Main data collection (180 concepts), then main runs, then analysis scripts unchanged from the pilot.

## 11. Threats to validity

Sampling bias (Section 3); BioPortal US Edition only; IS_A only; English labels; model versions change and are deprecated (raw outputs archived); prompt sensitivity (one fixed prompt per task, documented); task templates differ from real clinical questions; external providers may log prompts; no clinician or terminologist was involved, so clinical relevance is argued, not measured; hosted model behaviour can change behind a fixed identifier (mitigated by dated snapshots, provider pinning and archived raw outputs); a small budget limits the study to three models and one prompt.

## Amendments

**Amendment 1 (2026-11-03, before any data collection or model run).** Reasons: decisions from the author and supervisor, plus provider changes found while costing the study.
1. *Models:* the v1.0 Groq models (Llama 3.1 8B Instant, Llama 3.3 70B Versatile) became enterprise-only on Groq in August 2026 (per a third-party pricing tracker; Groq's own page could not be checked from here). Replaced by the three pinned OpenRouter models in Section 6.
2. *Budget:* US$20–30 total, one month of API access; enforced with `--max-cost`.
3. *Human evaluation:* no clinical annotator. Question (b) changed from "clinically misleading" to "contradicts the structured claims", annotators blinded, clinical appropriateness declared out of scope (Section 9).
4. *Licensing:* live collection deferred until a SNOMED CT licence is in place (Section 3; `docs/LICENSING.md`).
