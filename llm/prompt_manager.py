"""
Versioned prompt templates (protocol Section 4). The version string is
stored with every result; change it whenever any text below changes.

The system prompt is identical across conditions and context formats, so the
only manipulated variable is the evidence block itself.
"""

PROMPT_VERSION = "v2.0"  # v2.0: claims cite evidence edge IDs (protocol v2.0)

SYSTEM_PROMPT = (
    "You answer questions about the SNOMED CT clinical terminology hierarchy. "
    "Concepts are identified by numeric SNOMED CT concept IDs. "
    "'X IS_A Y' means X is a subtype of Y. "
    "You are given evidence retrieved from a terminology server. The evidence may be incomplete. "
    "You may also use your own knowledge, but you must label every claim with its source: "
    "\"evidence\" if the claim is stated in the evidence, otherwise \"model_knowledge\". "
    "For every \"evidence\" claim, cite the IDs of the evidence edges (E1, E2, ...) that support it; "
    "cite nothing for \"model_knowledge\" claims and never cite an edge ID that is not shown. "
    "Use concept IDs exactly; never invent IDs. "
    "If you cannot answer reliably, set \"insufficient_evidence\" to true. "
    "Reply with one JSON object and nothing else."
)

CLAIM_HELP = (
    'Each claim is {"subject": "<ID>", "relation": "<relation>", "object": "<ID or null>", '
    '"source": "evidence" or "model_knowledge", "cites": ["<edge ID>", ...]}. '
    'Relations: "direct_parent" (subject IS_A object in one step), '
    '"ancestor" (subject IS_A object in one or more steps), "no_children" (subject has no subtypes; object null), '
    '"no_parents" (subject has no parents; object null).'
)

QUESTIONS = {
    "T1": ("List all direct parents (one IS_A step) of {x}.",
           '{{"answer": ["<parent ID>", ...], "claims": [...], "insufficient_evidence": false}}'),
    "T2": ("List all direct children of {x}, i.e. concepts that are directly IS_A {x}. "
           "If it has no children, return an empty list and a no_children claim.",
           '{{"answer": ["<child ID>", ...], "claims": [...], "insufficient_evidence": false}}'),
    "T3": ("Is {x} a kind of {y}, directly or through intermediate concepts?",
           '{{"answer": "yes" | "no" | "unknown", "claims": [...], "insufficient_evidence": false}}'),
    "T4": ("Give a chain of direct IS_A steps from {x} up to {y}, as a list of concept IDs that starts "
           "with {x_id} and ends with {y_id}.",
           '{{"answer": ["{x_id}", "<ID>", ..., "{y_id}"], "claims": [...], "insufficient_evidence": false}}'),
    "T5": ("Explain where {x} sits in the SNOMED CT hierarchy: its direct parents, its more general "
           "ancestors, and whether it has subtypes. Keep the explanation under 120 words, and list "
           "every hierarchical statement it makes as a claim.",
           '{{"answer": "<explanation>", "claims": [...], "insufficient_evidence": false}}'),
    "T6": ("What is the nearest common ancestor of {x} and {y}, i.e. the most specific concept that both "
           "are kinds of? If several are equally specific, list all of them. Support each with ancestor "
           "claims for both concepts.",
           '{{"answer": ["<ancestor ID>", ...], "claims": [...], "insufficient_evidence": false}}'),
}


def concept_ref(concept_id, label):
    return f"{label} [{concept_id}]" if label else f"[{concept_id}]"


def build_messages(item, context_text, label_of):
    """Chat messages for one item. ``label_of`` maps concept ID to preferred label."""

    question, schema = QUESTIONS[item.task]
    fill = {
        "x": concept_ref(item.concept_id, label_of(item.concept_id)),
        "x_id": item.concept_id,
        "y": concept_ref(item.target_id, label_of(item.target_id)) if item.target_id else "",
        "y_id": item.target_id or "",
    }
    user = (
        "EVIDENCE\n" + context_text + "\n\n"
        "QUESTION\n" + question.format(**fill) + "\n\n"
        "Reply with JSON of this form:\n" + schema.format(**fill) + "\n" + CLAIM_HELP
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
