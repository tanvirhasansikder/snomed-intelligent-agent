# Research protocol v2.0

**Faithful by Construction? Measuring and Verifying LLM Explanations of SNOMED CT Hierarchy over Partial, On-Demand Retrieved Subgraphs**

* Written 2026-11-04, before any real SNOMED CT data was collected or any experimental model call was made. It replaces v1.1 (2026-11-03); the change log is at the end.
* The design is fixed here. Prompt texts and the evidence format are frozen at the end of milestone M2, before the main data collection, under the Git tag `protocol-v2.0-frozen`.
* Any later change goes into "Amendments" with a date and reason.
* Pilot outputs are never reported as findings.
* Context: CSE499 project, North South University; deadline 2026-12-03.

## 1. Problem

The system retrieves SNOMED CT **on demand** from BioPortal rather than loading a full release. The subgraph a model sees is therefore partial:
* ancestors stop at a depth limit;
* children are often not retrieved;
* retrieval can fail.

A deterministic graph query already answers a structured question such as "what are the parents of X?" correctly when the evidence is complete. An LLM adds value only where structured queries are awkward: natural-language explanations, multi-step reasoning (paths, common ancestors) and comparisons. Those are exactly the places where it can state relationships that are not in the evidence.

The study asks three things:
* how often such statements occur;
* how they depend on what was retrieved;
* whether a deterministic, claim-level verifier can make explanations faithful to the retrieved evidence, and at what cost.

**What is known.** See `docs/LITERATURE_SEARCH.md`.
* KG-based RAG degrades when the graph is incomplete, and models fall back on memorised knowledge (Zhou et al., EACL 2026; Xu et al., EMNLP 2024).
* Models rarely abstain when the context is insufficient (Joren et al., ICLR 2025).
* Ontology-grounded generation reduces hallucination on a complete local graph (Ali et al., JBI 2026).
* LLMs hallucinate SNOMED CT codes (Born et al., 2026).

**What is open.** We found no study of claim-level faithfulness of LLM explanations of a clinical terminology hierarchy under partial, on-demand retrieval. Such a study would need:
* sufficiency measured deterministically from the graph;
* a verifier that separates "true but not retrieved" from "false".

## 2. Contributions

These are claims to be tested, not results.

* **C-A: question-aware partial-subgraph retrieval (S1).** Retrieval scope depends on the question type: upward, downward or both.
* **C-B: deterministic claim-level verification (S2).** Claims are typed triples with edge citations. They are checked against the retrieved subgraph; unsupported claims trigger one controlled regeneration and are then removed.
* **C-C: measurement of how retrieval completeness affects faithfulness.** A paired comparison of upward-only retrieval (B1) with question-aware retrieval (S1), stratified by a deterministic sufficiency label for every item, with three-way claim labels from a frozen reference snapshot.

## 3. Research questions and hypotheses

| ID | Question | Hypothesis / analysis |
| --- | --- | --- |
| RQ1 | Without retrieval, how reliable are LLM statements about SNOMED CT hierarchy? | **H1:** closed-book (B0) false-claim and identifier-hallucination rates are above zero and rise with concept depth (trend test). |
| RQ2 | Does question-aware retrieval improve correctness and faithfulness over upward-only retrieval? | **H2 (primary):** S1 scores higher than B1 and has a lower unsupported-claim rate, mainly on items where B1's evidence is insufficient (children, deep is-a, paths, common ancestors). |
| RQ3 | How does retrieval completeness affect explanation reliability? | **H3 (primary):** within B1 and S1, items with insufficient evidence have higher unsupported-claim (fill-in) and false-claim rates than items with sufficient evidence (tested under `sufficient` and repeated under `answer_in_evidence`). False absence claims ("X has no subtypes") occur mainly when children were not retrieved. |
| RQ4 | What does deterministic verification gain and cost? | **H4 (primary):** S2 has fewer items with a false claim than S1. Also reported, descriptively: claims removed, split into true-but-not-retrieved vs false (the price of strictness); change in task score; regeneration success rate. That S2 has no claims unsupported by its evidence is guaranteed by construction, so it is not tested. |
| RQ5 | What are the trade-offs? | Descriptive: faithfulness vs completeness vs tokens vs latency vs BioPortal requests, per system. |

H2, H3 and H4 are the primary hypotheses; H1 is secondary. Holm correction is applied within each family.

## 4. Data: frozen reference snapshot

* **Source:** BioPortal `SNOMEDCT` (SNOMED CT US Edition). The release is recorded from `latest_submission`. Every API response is stored with a timestamp (`evaluation/bioportal_cache.py`), and all experiments replay this snapshot offline. Retrieval is therefore deterministic, and the reference is fixed.
* **Sampling** (`evaluation/collect.py`):
  * six top-level hierarchies: Clinical finding 404684003, Procedure 71388002, Body structure 123037004, Substance 105590001, Pharmaceutical/biologic product 373873005 and Organism 410607006;
  * each is validated as a child of the root 138875005 or, as BioPortal serves SNOMEDCT, as a top concept: the concept exists and has no parents (Amendment 2). The check used is recorded in the data set;
  * seeded random descent through direct children to a target depth drawn uniformly from 2–8 below the hierarchy's top concept, stopping at leaves.
* **Depth** of a concept = the number of IS_A steps to the top concept of its hierarchy (shortest and longest path). In BioPortal's SNOMEDCT this is one less than the depth below the SNOMED CT root.
* **Size:**
  * **main study:** 20 concepts per hierarchy, 120 in total, seed 2026110202;
  * **pilot:** 2 per hierarchy, 12 in total, seed 2026110201, excluded from the main set.
  * The size is set by the one-month schedule and the API quota (Section 8).
* **Reference closure per concept:**
  * label, direct parents, and direct children with a completeness flag;
  * the BioPortal `/ancestors` set;
  * the parents of every ancestor;
  * the children of each direct parent (for sibling negatives).
* **Known bias:** random descent over-samples concepts under nodes with few children. Depth and hierarchy are reported for every result.
* **Data handling:** snapshots and results stay in `data/` and `results/` (Git-ignored) and are not redistributed. Licensing for publication is handled by the author and supervisor. No SNOMED CT content is sent to a provider in tests; experiment prompts do contain SNOMED CT labels (Section 11).

**The reference is used only for evaluation.** Systems at run time see only their retrieved evidence. The verifier in S2 checks against the retrieved evidence only, as it would when deployed. The three-way distinction (supported / true but not retrieved / false) is made **afterwards**, against the frozen reference. Without that reference, a claim that is true but not retrieved cannot be told apart from a false one, and we do not claim otherwise.

## 5. Question types (automatic gold)

| Task | Question | Gold (from the snapshot) | Primary score |
| --- | --- | --- | --- |
| T1 | Direct parents of X | set of IDs | set F1 (also P, R) |
| T2 | Direct children of X, where X is the sampled concept's parent on the sampling walk (Amendment 4); one question per distinct parent, only where its gold child list is complete and has ≤ 100 children | set, possibly empty | set F1; "no children" claims scored as absence claims |
| T3 | Is X a kind of Y? Positive: Y is an ancestor ≥ 2 steps away (the hierarchy's top concept excluded, as trivial). Negative: Y is a sibling of X. | yes / no | accuracy |
| T4 | IS_A path from X to its top-level concept | every valid upward path | exact validity (valid chain reaching the target); edge precision; **edge F1** against the best-matching gold path |
| T6 | Nearest common ancestor of X and Z, where Z is another sampled concept of the same hierarchy (seeded pairing) | set of lowest common ancestors (LCA set; can have more than one in a polyhierarchy) | hit (answer ∈ LCA set); set F1 when several are given |
| T5 | Explain where X sits in the hierarchy | structured claims plus text | claim labels (Section 7); completeness = recall of X's gold parents, and whether the child status is stated correctly |

Every answer is JSON containing:
* the answer;
* typed claims (`direct_parent`, `ancestor`, `no_children`, `no_parents`), each with `source: evidence | model_knowledge` and the **IDs of the evidence edges it cites**;
* an `insufficient_evidence` flag.

**Two evidence labels per item and system** (Amendment 1), both computed deterministically:
* **`sufficient`:** the evidence can *certify* the answer, using only the evidence and its coverage records. R0 answers only when this is true. For example:
  * T1 is sufficient if X's parent list is complete;
  * a negative T3 item is sufficient only if X's full ancestor closure is present;
  * T6 is sufficient only if both closures are present, because in a polyhierarchy a second lowest common ancestor may lie beyond a depth limit.
* **`answer_in_evidence`:** the evidence *contains* the full gold answer. This is computed in evaluation from the frozen gold, which no system sees. It differs from `sufficient` mainly for T6, where both concepts can reach their lowest common ancestor inside the window even though the window cannot rule out a second one. It also differs for T1 and T2 when the list is present but its completeness was not recorded.

RQ3 results are reported under both labels.

## 6. Systems

| Code | System | Retrieval (offline replay of the snapshot) | Status on 2026-11-04 |
| --- | --- | --- | --- |
| R0 | Deterministic template answer, no LLM | Run on B1 and on S1 evidence, giving an evidence-only reference for each | implemented for T1–T5 under the older retrieval settings; T6 and S1 evidence: M2 |
| B0 | Closed-book LLM | none | implemented (setting `C0`) |
| B1 | LLM + upward-only retrieval | the app's current crawler: upward BFS to depth 2, no children (setting `U2`) | implemented |
| S1 | LLM + question-aware retrieval | per question type (below) | M2 |
| S2 | S1 + deterministic verification + one controlled regeneration | as S1 | verifier and filter implemented; citations, the free-text check and regeneration: M2 |
| B2 (optional) | LLM-generated read-only Cypher over Neo4j loaded with the frozen snapshot | the whole snapshot (more data than S1; stated as a caveat) | go/no-go decision by 2026-11-21 |

**S1 retrieval planner.** The planner gets the question type: it is known for benchmark items, and the UI classifies it with rules. It then retrieves:

| Question type | Retrieval |
| --- | --- |
| T1 | parents of X |
| T2 | children of X (paged) |
| T3, T4 | upward closure of X |
| T6 | upward closures of X and Z |
| T5 | parents up to depth 2 plus the children of X |

There is a request budget per question (default 60 BioPortal requests). If the budget runs out, the evidence is marked incomplete, so S1 is partial by design rather than complete by assumption.

**Evidence format, identical for B1, S1 and S2,** so that B1 and S1 differ only in what was retrieved:
* numbered edges `E1: X IS_A Y`;
* per concept, whether its parent list and child list are complete, partial, failed or not retrieved;
* the depth frontier.

**S2 verification:**
1. Parse the claims.
2. Label each claim against the retrieved evidence, and check its cited edges.
3. Check that every concept ID mentioned in the free text appears among the claims.
4. If any claim is unsupported, or any ID is unlisted, make **one** regeneration request. It lists the exact unsupported claims and asks the model to remove them or mark them as insufficient evidence.
5. Remove any claim that is still unsupported, and turn answers left without support into "insufficient evidence".

We use deterministic external feedback because intrinsic self-correction without such feedback is unreliable (Huang et al., ICLR 2024; compare the self-verification approach of Dhuliawala et al., Findings of ACL 2024). Generated claims never modify the stored graph.

**Models:**
* **Primary:** `gemini-3.5-flash-lite` through Google's OpenAI-compatible endpoint, temperature 0, `reasoning_effort=low`, JSON mode, max 8,192 output tokens (800 before Amendment 3; 2,000 before Amendment 5). It is a fixed, non-preview identifier and passed the M0 smoke test on 2026-11-04 (synthetic evidence only).
* **Optional second model (open weights):** `gemma-4-31b-it` through the same API. To be smoke-tested first.
* Every call records the model ID returned, settings, prompt version, tokens, latency, finish reason and raw output.
* **Nondeterminism:** 3 repetitions on a random 10% of items.

## 7. Claim labels and metrics

Each claim is labelled by `explanation/explainer.py`:

| Label | Meaning |
| --- | --- |
| `supported_by_subgraph` | entailed by the retrieved evidence |
| `true_not_retrieved` | true in the reference but not in the evidence, i.e. parametric fill-in |
| `false_in_reference` | false in the reference |
| `contradicted_by_complete_evidence` | conflicts with evidence that is complete |
| `unknown_identifier` | an ID not in the reference |
| `insufficient_evidence` | the reference cannot decide |

**Metrics:**
* **Task:**
  * P/R/F1 (T1, T2, T6);
  * accuracy (T3);
  * exact validity, edge precision and edge F1 (T4);
  * LCA hit (T6);
  * completeness (T5).
* **Faithfulness:**
  * **unsupported-claim rate:** claims not supported by the retrieved evidence;
  * **false-claim rate:** false in the reference;
  * **true-not-retrieved rate**;
  * **identifier hallucination rate:** cited IDs that are unknown or have an invalid check digit;
  * **misattribution rate:** claims tagged `evidence` but not supported;
  * **false-absence rate**;
  * **unlisted ID mentions** in the free text.
* **Citations,** adapting the statement-level citation recall and precision of Gao et al. (EMNLP 2023), with graph entailment in place of an NLI model:
  * **citation precision:** claims whose cited edges exist in the evidence and entail the claim, as a share of claims that cite anything;
  * **citation recall:** supported claims with an entailing citation, as a share of supported claims.
* **Behaviour:**
  * abstention rate, and accuracy conditional on sufficiency;
  * for S2: regeneration rate and claims removed, split by reference label.
* **Efficiency:**
  * prompt and completion tokens per question;
  * LLM latency;
  * BioPortal requests per question, counted in replay;
  * live retrieval latency, measured separately on 30 questions against BioPortal, because replay timing does not reflect it.

## 8. Analysis and sample size

* **Paired comparisons on identical items:**
  * primary contrasts: B1 vs S1 (H2) and S1 vs S2 (H4);
  * secondary: B0 vs B1.
* **Tests:**
  * binary outcomes (T3 correct, T4 valid, T6 hit, "item has a false claim"): exact McNemar test;
  * per-item F1: Wilcoxon signed-rank test;
  * rate differences: paired bootstrap over items (claims are clustered within items);
  * H1: Cochran–Armitage trend test over depth bins;
  * H3: sufficient vs insufficient items within each system, with a bootstrap CI for the difference, and the item-level McNemar test where pairing exists (B1 vs S1 on the same item).
* **Reporting:**
  * 95% percentile bootstrap CIs (1,000 resamples, seed 0) for every rate;
  * effect sizes with every p-value;
  * results per model, never pooled across models;
  * results by question type, hierarchy and depth.
* **No result is dropped.** Failed or unparseable responses are counted as failures.
* **Calls and quota:**
  * about 120 concepts × 6 question types gives roughly 650 items, after T2 and T6 eligibility;
  * the LLM systems need about B0 + B1 + S1 + about 0.3 regeneration ≈ 3.3 calls per item, about 2,200 calls, plus the repetitions: **about 2,400 calls per model**.
  * On the free tier (2026-11-04: Flash-Lite 15 requests/minute and 500/day; Gemma 4 31B 30/minute, 14,400/day and 16K input tokens/minute), Flash-Lite needs about 5 days and Gemma about 3 hours.
  * If the daily cap threatens the schedule, billing is enabled; this is recorded as an amendment.

## 9. Human check of the structured-claim assumption (minimal)

Scoring relies on the structured claims the model lists. We check how far its free text agrees with them:
* The author labels 50 T5 explanations, stratified and blind to system and model, using `docs/ANNOTATION_GUIDE.md`.
* Questions: (a) does the text contain a hierarchical claim missing from the claim list? (b) does the text contradict the claims?
* A test–retest relabelling of 20 items after at least 7 days gives intra-rater agreement (Cohen's κ).
* A larger, two-annotator evaluation is optional.
* Clinical appropriateness is **out of scope**; the project makes no claim about clinical safety or usefulness.

## 10. Procedure and milestones

| Milestone | Dates | Work |
| --- | --- | --- |
| M0 | Nov 4–5 | Windows verification (done: 294 tests, dry run); Gemini smoke test (done); this protocol |
| M1 | by Nov 10 | Evidence v2 (edge IDs, coverage); shared single-question pipeline (`llm/agent.py`); citations in the answer schema |
| M2 | by Nov 17 | S1 planner; T6; verifier upgrades; S2 regeneration; pilot (12 concepts); freeze prompts and evidence format (`protocol-v2.0-frozen`) |
| M3 | by Nov 24 | Main snapshot (120 concepts); runs; analysis; 30-item error analysis; live latency measurement; B2 decision (Nov 21) |
| M4 | by Dec 1 | Streamlit "ask and verify" page with offline replay fallback; report; slides; rehearsal |
| Buffer | Dec 2–3 | — |

The pilot checks parsing, quota, timing and bugs only. Analysis scripts are fixed before the main run.

## 11. Threats to validity and limitations

* **Data:**
  * sampling bias (Section 4);
  * one source (BioPortal, US Edition), IS_A only, English labels;
  * the gold is a BioPortal snapshot, not an RF2 release, and is checked against `/ancestors` for consistency.
* **Task design:**
  * template questions differ from real user questions;
  * **lexical cues:** concept labels can reveal hierarchy (e.g. "Bacterial pneumonia" contains "Pneumonia"), so a model may infer an unretrieved relationship from the label rather than from memorised knowledge. Such fill-in still counts as unsupported by the evidence. Where the gold confirms it, it is counted as true-but-not-retrieved; we do not claim to separate lexical inference from recall. The synthetic test ontology encodes its hierarchy in labels and is used only for software testing;
  * one fixed prompt per task;
  * in the benchmark the question type is given to S1 (the UI's rule-based classifier is evaluated only informally);
  * for two-concept questions (T6), the fixed baselines B0/B1 also expand the second concept with their own settings, so S1's advantage is not an artefact of B1 ignoring the second concept;
  * B2, if run, sees more data than S1.
* **Sampling:** the sampling walk usually ends at a leaf (all 12 pilot concepts were leaves), so T1, T3–T6 are asked mostly about leaf concepts, and T2 is asked about their parents (Amendment 4).
* **Models:**
  * **nondeterminism at temperature 0:** in the pilot, repetitions of the same request gave a different answer on 3–4 of 6 repeated items per system (including answering vs abstaining). Single-run differences between systems therefore include model noise; the repeated subset reports its size;
  * hosted models change behind fixed names; mitigated by non-preview identifiers, recorded responses and archived raw outputs;
  * a small model set (one, or optionally two, models).
* **Provider and data:**
  * free-tier provider terms may allow prompts to be used by the provider; paid tiers differ. This is disclosed, and billing can be enabled.
* **Evaluation:**
  * claim scoring depends on the model listing its claims (Section 9 checks this);
  * no clinician or terminologist was involved;
  * no claim of clinical utility.

## Change log and amendments

* **v1.0 (2026-11-02):** coverage-aware grounding design. Retrieval settings C0, U1, U2, U2D and FULL; plain vs coverage-annotated context; three OpenRouter models.
* **v1.1 (2026-11-03):** Groq models replaced; budget cap; non-clinical annotation; licensing prerequisite.
* **v2.0 (2026-11-04), decided by the author with the supervisor, before any data or experimental call:**
  1. **Systems:** the author's systems R0, B0, B1, (B2), S1 and S2 replace the v1 setting × format grid. B0 = C0 and B1 = U2 are kept. The question-aware planner (S1) and regeneration (S2) are added. U2D, FULL and the plain-vs-annotated comparison become optional ablations; S1 always uses the annotated evidence format.
  2. **Question types:** T6 (nearest common ancestor) added. Edge F1, citation precision/recall, unlisted-ID mentions and efficiency metrics added.
  3. **Models:** Gemini replaces OpenRouter. `gemini-2.5-flash` is unavailable to new users, and `gemini-3.8-flash` allows only 20 requests per day on the free tier. `gemini-3.5-flash-lite` was chosen; Gemma 4 31B is optional. The OpenRouter configurations are retired at M2.
  4. **Scope:** sample size 180 → 120 concepts and pilot 30 → 12 (schedule and quota); human evaluation reduced to one annotator with a test–retest check; the licensing section is replaced by data-handling rules (licensing is handled by the author).

* **Amendment 1 (2026-11-04, during implementation, before any data or experimental call).** Reason: a synthetic test run showed B1 answering a T6 question correctly from evidence that was labelled insufficient.
  1. The `answer_in_evidence` label was added alongside `sufficient` (Section 5), and RQ3 is reported under both.
  2. B0/B1 expand both concepts of a T6 question (Section 11).
  3. Lexical cues were added as a threat to validity (Section 11).

* **Amendment 2 (2026-11-05, at the first pilot collection, before any concept was sampled or stored).** Reason: in BioPortal's SNOMEDCT (US Edition via UMLS), the root 138875005 exists but lists no children, and the six top-level hierarchy concepts have no parents (checked live). Collection stopped at the hierarchy check, and nothing was saved.
  1. A hierarchy is accepted if it is a child of the root or a top concept with no parents. The check used is recorded per hierarchy (`hierarchy_validation`).
  2. Depth is measured to the hierarchy's top concept (a concept whose retrieved parent list is empty) instead of to 138875005 (Section 4).
  3. T3 positive targets exclude that top concept instead of the root (Section 5).
  The synthetic test ontology keeps a root and is unaffected.

* **Amendment 3 (2026-11-05, pilot, after the first 10 calls and before the full pilot run).** Reason: one of the first 10 pilot answers (B0, T5) stopped with `finish_reason=length` after only 192 reported completion tokens, below the 800-token limit. The likely cause is hidden reasoning tokens, which count against the limit but are not included in the reported completion tokens. These 10 answers were inspected for errors, parsing and timing only; no score was computed.
  1. `max_tokens` is raised from 800 to 2,000 for every model in the pilot and main configurations. The limit is part of the cache key, so the 10 earlier answers are requested again and are not used.
  2. Each call records the hidden reasoning tokens when the provider reports them (`reasoning_tokens`). The report adds the mean reasoning tokens and the truncation rate (`finish_reason=length`) per system.
  3. Truncated answers stay in the analysis and are scored as they are (an unparseable answer counts as wrong). The truncation rate is reported for every system.
  4. Each row records the model settings it was made with (`model_settings`). A row counts as finished only if these match the configuration, so rows made before a settings change (or before settings were recorded) are collected again, and the report keeps the latest row. This was found when the first re-run kept the 10 rows made at 800 tokens.
  5. The report keeps, for each model, only rows made with its latest settings, so an old answer cannot stand in for one whose retry failed (seen in the pilot when a retry got HTTP 503).
  6. Gemini's OpenAI-compatible endpoint did not report reasoning tokens in the pilot (`reasoning_tokens` empty in all rows), so hidden reasoning cannot be measured. The truncation rate is the check that the limit is high enough.

* **Amendment 4 (2026-11-05, pilot review, before the protocol freeze).** The full pilot (64 items, 432 rows) had no errors, a 100% parse rate and no truncated answers. Reviewing it (operational checks only; no score was interpreted) found:
  1. **Paging bug (fixed).** No T2 items were generated: every sampled concept's child list was stored as "0 children, incomplete" after 20 pages. A live check showed these are leaf concepts, and that BioPortal answers a leaf's `/children` with `"nextPage": null` but a non-empty `links.nextPage`. The client read the link, kept paging and marked the list incomplete, so leaves were also shown to the models as having a *partial* child list. The client now treats a top-level `nextPage` as authoritative and stops when `page >= pageCount`. Child lists of non-leaf concepts were paged correctly (checked live for all 12 parents). The pilot data are collected again with the fixed client, and the pilot is run again.
  2. **T2 target (Section 5).** All 12 pilot concepts were leaves, so "children of the sampled concept" would always be the empty set. T2 now asks about the sampled concept's parent on the sampling walk, which has at least one child; one question per distinct parent. Collection also stores the direct parents' concept records (`get_concept`) so the retrieval can start there.
  3. **Run order.** The pilot ran all B0 answers, then B1, S1 and S2. Mean API latency was 12.5 s for B0/B1 (00:45–01:21) and 3.1 s for S1 (01:21–01:28), so latency reflected the time of day. The runner now interleaves by item: B0, B1, S1, S2 on one item, then the next item (S1 still precedes S2). Interleaving spreads the API's time-of-day variation evenly over the systems; LLM latency is still reported as descriptive only, and live retrieval latency is measured separately (Section 7).
  4. **Nondeterminism.** Repeated identical requests at temperature 0 differed on 3–4 of 6 items per system, in content and not only in order. This is added as a threat to validity (Section 11); the repeated-subset design is unchanged.

* **Amendment 5 (2026-11-05, second pilot run on the corrected data, before the protocol freeze).** The run (75 items, 498 rows) had no errors. Reason: 4 of the 11 new T2 answers from S1 and S2 (36%) stopped with `finish_reason=length` at 2,000 tokens. These T2 concepts have up to 80 children, and the model writes one claim with a citation per child.
  1. **Parser (fixed).** The reply parser returned the first complete JSON object in the text. In a cut-off reply the outer object never closes, so the parser returned the first *claim* as if it were the reply, and the answer was read as an empty list without a parse error. The parser now accepts only an object that has the required `answer` field; a cut-off reply is a parse failure and is scored as wrong (Section 8, "no result is dropped").
  2. **Output limit.** `max_tokens` is raised from 2,000 to 8,192 for every model in the pilot and main configurations (a T2 answer of 100 children with claims and citations needs roughly 4,000–5,000 tokens). The limit is part of the cache key and of each row's recorded settings, so the whole pilot is collected again.
  3. **Retrieval completeness in the pilot (observation, no change).** With the corrected child lists, S1's evidence was sufficient for every pilot item (75/75), while B1's was sufficient for 32%. Incomplete-evidence cases in the main study therefore come mainly from B1 and from the budget ablation.

* **Pilot completed (2026-11-06); protocol frozen as `protocol-v2.0-frozen`.** Third pilot run, with Amendments 3–5 applied: 75 items (12 sampled concepts, 11 T2 parents), 498 rows (150 R0, 87 per model system), collected over two days because of the free-tier daily quota. Operational checks only, no score interpreted: 0 errors, 100% parse rate and 0% truncated answers for every system and task (mean S1 completion tokens on T2: about 1,770 of 8,192). Agreement of repeated answers at temperature 0 (6 repeated items per system): B0 0.50, B1 0.83, S1 0.83, S2 0.67. The pilot items are excluded from the main sample (`exclude_samples_from`). Any later change is a new, dated amendment.

