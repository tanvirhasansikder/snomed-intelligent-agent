# SNOMED CT licence for this study (checked 2026-11-03)

This is not legal advice. Confirm every step with SNOMED International (info@snomed.org).

## Why a licence is needed

* **Bangladesh is not a Member.** SNOMED International lists 54 Members, and Bangladesh is not one of them ([Members](https://snomed.org/members)). SNOMED CT is free to use only in Member territories, low-income countries, or for approved research projects. In other countries, use must be registered and fees may apply ([NLM: SNOMED CT licensing](https://www.nlm.nih.gov/healthit/snomedct/snomed_licensing.html)).
* **BioPortal's rules without a licence.** BioPortal's terms limit users who have no SNOMED International licence to *exploring and evaluating* SNOMED CT. Such users may not use it in a data analysis system, copy SNOMED CT identifiers into any database or document, or distribute SNOMED CT content ([BioPortal terms of use](https://www.bioontology.org/terms/)).
* **What this study does.** It stores BioPortal responses on disk and analyses them. The paper will also contain concept identifiers. Both go beyond "exploring and evaluating". Storing concepts in Neo4j, as the Streamlit app does, is likewise covered only by a licence.
* **Why a UMLS licence alone does not help.** The NLM page says users in non-member countries must register their use with SNOMED International through MLDS, and should apply to SNOMED International directly.

## The route: free research fee waiver

SNOMED International offers fee waivers for *qualifying research projects* that are not meant for commercial use ([Fee exemptions](https://snomed.org/fee-exemptions)). To apply:

1. Email **info@snomed.org**.
2. Attach a formal, signed letter on official letterhead, addressed to the SNOMED International CEO. It must be signed by a representative of the university.
3. Include:
   * a project description
   * the names of the project leader and other stakeholders
   * the project end date
   * the expected outcomes
4. If approved, you receive a fee waiver agreement. It requires you to report progress, findings and any implementations every year.

You will probably also need to register in the Member Licensing and Distribution Service (MLDS, <https://mlds.ihtsdotools.org>) as an Affiliate in a non-member territory. Ask in the email whether that is needed, and in which order.

**Timing:** unknown. Send the request now. Until it is approved, run only the synthetic dry run, and do not buy LLM credits: the one-month API budget should start when the data can be collected.

**Fallback if refused or very slow:** run the same study on an openly licensed biomedical ontology that BioPortal also serves, such as MONDO, the Human Phenotype Ontology or the Disease Ontology. Check each ontology's licence. This would need code changes: identifier validation, roots and the hierarchy list. It would also change the paper's title and its SNOMED-specific claims. Decide on this only if the waiver fails.

## Draft email (edit before sending)

> **To:** info@snomed.org
> **Subject:** Research fee waiver request: non-commercial university research project (Bangladesh)
>
> Dear SNOMED International team,
>
> I am an undergraduate researcher at North South University, Dhaka, Bangladesh. Under the supervision of [supervisor name, title], I am carrying out a non-commercial research project that evaluates how large language models describe the SNOMED CT IS_A hierarchy when they are given only a partially retrieved part of it.
>
> The project reads SNOMED CT (US Edition, current release) through the NCBO BioPortal API. It stores a sample of about 200 concepts and their ancestors locally for analysis, and will be reported in a peer-reviewed journal article. No SNOMED CT content will be redistributed, and nothing will be deployed in clinical use or for commercial purposes.
>
> As Bangladesh is not a Member territory, I would like to request a research fee waiver. Please find attached a letter from the university. Could you also tell me whether I should register in MLDS as an Affiliate first, and whether the article may list the identifiers of the sampled concepts?
>
> Kind regards,
> Md. Tanvir Hasan Sikder
> Department of Electrical and Computer Engineering, North South University
> tanvir.sikder@northsouth.edu

## Draft university letter (on NSU letterhead, signed by the supervisor or chair)

> [Date]
>
> To: Chief Executive Officer, SNOMED International
>
> **Re: Request for a SNOMED CT research fee waiver**
>
> North South University confirms that Md. Tanvir Hasan Sikder, an undergraduate student in the Department of Electrical and Computer Engineering, is conducting the research project "Coverage-aware grounding: measuring parametric fill-in when large language models explain SNOMED CT hierarchy from partial, on-demand retrieved subgraphs" under my supervision.
>
> * **Project leader:** [supervisor name, title, email]. **Other stakeholders:** Md. Tanvir Hasan Sikder (student researcher).
> * **Description:** an offline evaluation of large language model answers about SNOMED CT IS_A relationships, using about 200 sampled concepts retrieved through NCBO BioPortal. The work is non-commercial and involves no patient data and no clinical deployment.
> * **Expected outcomes:** an undergraduate capstone report (CSE499) and one peer-reviewed journal article. Code is released openly; SNOMED CT content is not redistributed.
> * **Project end date:** [e.g. 31 December 2027].
>
> We request a fee waiver for this qualifying research project and agree to the annual reporting requirement.
>
> [Signature, name, title, department, North South University]
