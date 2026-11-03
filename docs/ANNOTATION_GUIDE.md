# Annotation guideline (protocol Section 9)

You will see 100 explanations written by language models about a SNOMED CT concept, one per row of your CSV. Work alone, and do not discuss rows with the other annotator until both files are finished. The model, the retrieval condition and the context format are hidden on purpose. Do not try to guess them.

Each row has:
* `explanation`: the model's free-text answer
* `structured_claims`: the claims the model listed in its JSON output, written as `relation(subject, object)`, for example `parent(233604007, 205237003)`

You do **not** judge whether a claim is medically true. That is checked automatically against SNOMED CT. You only compare the text with the model's own structured claims.

## Question 1: `extra_claim` (yes / no)

Does the explanation state a hierarchical relationship that is **not** in `structured_claims`?

* Hierarchical means: X is a kind of / subtype of / child of Y; X is a parent, ancestor or descendant of Y; X has or has no subtypes or children; or X lies on a path to Y.
* Count a claim even when it names concepts by label rather than ID, provided it is clear which concepts are meant.
* Repeating a listed claim in other words is **not** an extra claim.
* A general sentence with no specific concept pair ("SNOMED CT is a polyhierarchy") is **not** a claim.

Answer `yes` if there is at least one extra claim, otherwise `no`. Copy the extra claim into `notes`.

## Question 2: `contradicts_claims` (yes / no)

Does the explanation contradict the structured claims or the answer? Examples:
* the text says "X is a kind of Y", while a claim or the answer says it is not;
* the text says "X has no subtypes", while a claim lists a child of X.

A hedge ("probably", "may") that does not reverse a claim is **not** a contradiction. Answer `yes` or `no`, and note the conflict in `notes`.

## Practice and disagreements

* **Practice first.** Both annotators label the same 10 practice rows: export them with `--n 10` into a separate results folder, or use rows from the pilot. Compare and discuss them, and update this guide if needed. Practice rows are not counted.
* **Disagreements.** After kappa is computed, disagreements are discussed and settled. The original independent labels are the ones reported.
