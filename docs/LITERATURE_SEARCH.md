# Literature search log and novelty assessment

Purpose: decide what is already known before claiming any contribution. This is a documented, targeted search, **not** a systematic review (no PRISMA screening). Re-run and extend it before submission.

## Search log

| Date | Engine | Query | Notable results opened |
| --- | --- | --- | --- |
| 2026-10-17 | Web search (several engines via the assistant's search tool) | SNOMED CT + LLM; ontology RAG; LLM taxonomy knowledge; faithfulness/citation metrics; SNOMED hallucination | Chang & Sung 2024; Dong et al. 2024; Liu et al. 2024; Sun et al. 2024; He et al. 2023, 2024; Soman et al. 2024; Jin et al. 2024; Xiong et al. 2024; Luo et al. 2024; Gao et al. 2023; Min et al. 2023; Born et al. 2026; Schmid et al. 2025; Mavridis et al. 2025; Davidson et al. 2025 |
| 2026-11-02 | same | large language model SNOMED CT hierarchy subsumption question answering grounded knowledge graph 2025 2026 | Ali et al. 2026 (JBI) |
| 2026-11-02 | same | incomplete knowledge graph retrieval-augmented generation LLM hallucination missing triples evaluation | Zhou et al. 2026 (EACL; arXiv 2504.05163) |
| 2026-11-02 | same | "Generate-on-Graph" incomplete knowledge graph question answering | Xu et al. 2024 (EMNLP) |
| 2026-11-02 | same | ontology-grounded explanation faithfulness verification LLM biomedical ontology | Vaidya et al. (arXiv 2610.00682) |
| 2026-11-02 | same | "Sufficient Context" retrieval augmented generation ICLR 2025 | Joren et al. 2025 (ICLR) |
| 2026-11-02 | same | LLM SNOMED CT parent child hierarchy evaluation hallucination 2025 2026 | Lopez Osornio et al. 2026 (medRxiv) |
| 2026-11-02 | same | LLM taxonomy is-a ... abstain ... medical terminology | Uceda-Sosa et al. (arXiv 2405.20163) |
| 2026-11-02 | same | BioPortal SNOMED CT LLM explanation hierarchy navigation tool | Dilworth et al. 2025 (arXiv 2511.16698) |
| 2026-11-02 | same | knowledge graph completeness closed/open world assumption absence of evidence | Razniewski et al. 2024 (ACM Computing Surveys) |
| 2026-11-04 | same | Chain-of-Verification hallucination; LLMs cannot self-correct reasoning (for the S2 regeneration design) | Dhuliawala et al. 2024 (Findings of ACL); Huang et al. 2024 (ICLR) |
| 2026-11-18 | same (Study 2 novelty audit) | knowledge graph incompleteness RAG robustness missing triples; incomplete KGQA benchmark removed edges | Wang et al. 2026 MissDiag (arXiv 2608.18489); Zhou et al. 2026 BRINK (EACL); Xu et al. 2024 (EMNLP) |
| 2026-11-18 | same | sufficient context abstention; grounded refusal citation benchmark | Joren et al. 2025 (ICLR); Song et al. 2025 Trust-Score (ICLR) |
| 2026-11-18 | same | LLM told retrieved context is incomplete; completeness signal abstention | Min et al. 2026 CROWN-QA (arXiv 2608.04591); Zerhoudi et al. 2026 (ECML-PKDD) |
| 2026-11-18 | same | GraphRAG vs RAG systematic comparison | Han et al. 2026 (KDD; arXiv 2502.11371) |
| 2026-11-18 | same | SNOMED CT LLM journal; KG RAG biomedical journal; ontology-grounded RAG | Soman et al. 2024 (Bioinformatics); Hu et al. 2026 (arXiv 2608.28360); Sharma et al. 2025 OG-RAG (EMNLP); Do et al. (arXiv 2409.13746); Kaur 2026 (NeurIPS WiML poster); Ali et al. 2026 (JBI, re-confirmed via Crossref) |

Still to do before submission: PubMed and IEEE Xplore keyword searches with recorded hit counts; forward-citation search from Zhou et al. 2026, Joren et al. 2025 and Chang & Sung 2024.

## Closest prior work (all opened and checked)

| Work | What it shows | How our study differs |
| --- | --- | --- |
| [Zhou et al., "What Breaks Knowledge Graph based RAG?", EACL 2026, pp. 2522–2538](https://preview.aclanthology.org/credits/2026.eacl-long.114) (preprint [arXiv 2504.05163](https://arxiv.org/html/2504.05163v2)) | KG-RAG methods degrade when triples are deleted; they rely on memorisation; benchmark BRINK on Freebase | Open-domain Freebase, triples deleted at random; no hierarchy semantics, no claim-level faithfulness, no completeness signalling to the model |
| [Xu et al., "Generate-on-Graph", EMNLP 2024, pp. 18410–18430, doi:10.18653/v1/2024.emnlp-main.1023](https://preview.aclanthology.org/setup/2024.emnlp-main.1023) | Treats LLM as a source of missing triples in incomplete-KG QA; hallucination is the main error source | Treats parametric fill-in as a feature; in clinical terminology it is a risk that must be detected and attributed |
| [Joren et al., "Sufficient Context", ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/33dffa2e3d2ab74a783d1a8c292f66d9-Abstract-Conference.html) | Strong models often answer wrongly instead of abstaining when context is insufficient; sufficiency judged by an LLM autorater; selective generation helps | Text RAG with an autorater; we compute sufficiency **deterministically** from the graph and give the model explicit completeness metadata |
| [Ali et al., JBI 175:104993, 2026, doi:10.1016/j.jbi.2026.104993](https://www.sciencedirect.com/science/article/abs/pii/S1532046426000171) | Ontology-grounded GraphRAG (SPARQL) cut hallucination from ~48–63% to 1.7% on 60 COVID questions | Custom COVID ontology, complete local graph, question-level scoring; no incompleteness, no hierarchy navigation |
| [Uceda-Sosa et al., arXiv 2405.20163](https://arxiv.org/html/2405.20163v1) | LLMs are inconsistent on is-a hierarchies; KG prompt augmentation reduces inconsistency by up to about one third | Wikidata medical specialties; no partial retrieval or verification |
| [Sun et al., PVLDB 17(11), 2024](https://vldb.org/pvldb/vol17/p2919-sun.pdf) | Closed-book taxonomy accuracy generally falls from root to leaf | No SNOMED CT, no retrieval |
| [Born et al., Clinical NLP @ LREC 2026](https://aclanthology.org/2026.clinicalnlp-1.41/); [Schmid et al., GMDS 2025](https://conferences.publisso.de/en/conferences/gmds2025/25gmds055) | Frontier LLMs hallucinate SNOMED CT codes | Entity linking, not hierarchy explanation |
| [Liu et al., IEEE ICHI 2024](https://www.researchgate.net/publication/383326841_Using_Generative_Large_Language_Models_for_Hierarchical_Relationship_Prediction_in_Medical_Ontologies) | Open LLMs classify given SNOMED CT concept pairs as IS-A with macro-F1 up to 0.96 | Pair classification with candidates supplied; no retrieval, no explanations |
| [Dilworth et al., arXiv 2511.16698](https://www.emergentmind.com/papers/2511.16698) | Hierarchical retrieval for out-of-vocabulary queries on SNOMED CT | Concept retrieval, not explanation faithfulness |
| [Razniewski et al., ACM Computing Surveys 2024](https://arxiv.org/abs/2305.05403v2) | Survey of completeness, recall and negation in open-world KBs | Conceptual basis for completeness metadata; not about LLM grounding |
| [Vaidya et al., arXiv 2610.00682](https://arxiv.org/html/2610.00682v1) | Reasoner-verified MCQ benchmarks from OWL ontologies (incl. DOID) | Benchmark generation from complete ontologies; no partial retrieval |

### Added in the Study 2 novelty audit (2026-11-18, all opened)

Full matrix with data, metrics and numbers: the "Study 2 Novelty Audit and Literature Matrix" doc (claude.ai artifact MndKj26S19LUuG5gLv9kKw) and the project copy of this file.

| Work | What it shows | How Study 2 differs |
| --- | --- | --- |
| [Wang et al., MissDiag, preprint 2026](https://arxiv.org/pdf/2608.18489) | Removes support edges by four operators (random, source-context, relation-level, answer-adjacent) at severity 0.1/0.3/0.5; answer-adjacent removal hurts most (−10.3 to −21.3 F1 at 0.3) | Closest RQ1 design, but F1 only: no unsupported claims, abstention or coverage signal. We adopt its random-removal control idea |
| [Min et al., CROWN-QA, preprint 2026](https://arxiv.org/pdf/2608.04591) | Completeness cues decide whether absence justifies "no"; naive prompting over-closes 37–76%; partial lists often treated as complete | Closest RQ2 work; synthetic natural-language text, negatives only, no KG. Ours: retriever-generated per-list marks, all six tasks, crossed with removal level. We adopt its over/under-closure rates |
| [Song et al., Trust-Score, ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/4c88827decab6c046b881a2c3a99c76f-Abstract-Conference.html) | Grounded-refusal and citation metrics, NLI-judged | We compute grounded-refusal F1 with exact labels |
| [Han et al., RAG vs. GraphRAG, KDD 2026](https://arxiv.org/html/2502.11371) | Text RAG wins single-hop, graph wins multi-hop; token budgets differ | RQ4 replicates this on an ontology with exact gold; we add a size-matched text baseline |
| [Soman et al., Bioinformatics 40(9):btae560, 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11441322) | SPOKE KG-RAG raises disease-question accuracy | Accuracy only; no faithfulness or completeness |
| [Zerhoudi et al., ECML-PKDD 2026](https://arxiv.org/pdf/2606.29645) | Most context metadata lowers accuracy | Warns that coverage marks may hurt: report harms |
| [Hu et al., preprint 2026](https://arxiv.org/pdf/2608.28360) | Unsupported-claim rate and invalid citations in guideline KG QA | LLM judge equals generator; no controlled removal |
| [Kaur, NeurIPS 2026 WiML poster](https://neurips.cc/virtual/2026/179139) | SNOMED-based hybrid KG-RAG; GPT-4 unsupported claims 18.7% → 12.8% | Poster; no incomplete evidence or abstention |
| [He et al., Findings of ACL 2023](https://aclanthology.org/2023.findings-acl.213); [Sharma et al., EMNLP 2025](https://aclanthology.org/2025.emnlp-main.1674/); [Do et al.](https://arxiv.org/pdf/2409.13746) | Ontology subsumption probing; ontology-grounded RAG; GPT-4 confabulates rare ontology IDs | Context and metric support (invented-ID rate) |

## Assessment, updated 2026-11-18 (Study 2)

Removing knowledge-graph evidence in controlled amounts is **not** new (Xu et al. 2024; Zhou et al. 2026; Wang et al. 2026), and neither is "insufficient context causes hallucination" (Joren et al. 2025) or "graph helps multi-hop" (Han et al. 2026). Those prior studies measure accuracy only.

**Contribution Study 2 claims (if results hold, "to our knowledge"):** how controlled, answer-relevant evidence loss changes the evidential faithfulness of LLM answers about the SNOMED CT hierarchy — unsupported claims, appropriate abstention, over-claimed negatives, invented IDs — labelled deterministically against an exact reference, with a matched random-removal control; whether retriever-generated coverage marks change it (concurrent: Min et al. 2026); and what deterministic verification costs at each completeness level.

**Design changes adopted from this audit:** random-removal control at the 50% level; at least one withheld edge above 0%; faithfulness outcomes primary, accuracy secondary; size-matched text baseline; results split by whether the answer stays derivable through another path.

## Assessment (Study 1, our judgement, to be re-checked)

**Established:** (1) LLMs hallucinate SNOMED CT identifiers when ungrounded; (2) KG grounding reduces hallucination when the graph is complete; (3) KG-RAG degrades and falls back on memorisation when the graph is incomplete; (4) models often answer instead of abstaining when context is insufficient.

**Not found in this search:** a study that, for clinical terminology hierarchies,
1. constructs retrieval conditions from the *actual* on-demand crawler at controlled completeness levels;
2. labels each item's context sufficiency **deterministically** from retrieval provenance;
3. tests whether **explicit completeness metadata** (complete / truncated / not retrieved) in the context changes absence claims, abstention and source attribution;
4. scores every hierarchical claim against the retrieved edges and a frozen reference, separating "supported", "true but not retrieved" (parametric fill-in) and "false".

**Defensible contribution (if results hold):** an empirical characterisation of parametric fill-in under partial terminology retrieval, and a measured test of coverage-aware grounding. The engineering (crawler + provenance) supports the study but is not itself the contribution.

**Risks to novelty:** a 2025–2026 paper on completeness-aware or provenance-aware KG-RAG may exist outside these queries. Re-run the search before writing the related-work section.
