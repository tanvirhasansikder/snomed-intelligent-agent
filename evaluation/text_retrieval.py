"""
Text-retrieval baselines over the database's own concept cards (Study 2, RQ4).

* **L-RAG** (lexical): BM25 keyword search over ``concepts_fts`` (term,
  synonyms and card text), the database's own full-text index.
* **D-RAG** (dense): cosine similarity between a BGE-M3 vector of the question
  and the stored BGE-M3 card vectors (``concepts.embedding``, float32, 1024
  dimensions, unit length; ``meta.embedder = bge:BAAI/bge-m3``). The question
  vectors are computed separately (see ``dense_search``), so this module never
  loads the embedding model.

Queries are the names of the concepts the question is about (one for T1, T2
and T5; two for T3, T4 and T6), and the ``k`` cards are shared between them
(5 + 5 for two concepts, interleaved by rank). Only cards of concepts in the
International view are returned. This is entity-centred retrieval, the strong
form of the baseline: an earlier version that searched with the whole question
text matched instruction words ("step", "return", "empty") to unrelated cards
(text_retrieval_check on the v3 pilot, 2026-11-18), which would have made the
baseline a straw man. Neither system is told the task's structure; the graph
system's retrieval plan is what knows which relations each task needs.

What the model sees
-------------------
The retrieved cards, verbatim, with two additions so every system answers
with the same prompt and is verified the same way:

1. every concept a card names in its "Parent concepts", "Hierarchy" and
   "Includes" fields is followed by its concept ID, found by matching the name
   against the card concept's actual parents, ancestors on the stated path, or
   children in the database (never by a global name search, so it is exact);
2. every IS_A link a card states gets an edge ID (E1, E2, ...), the same IDs
   the graph systems' evidence uses, so claims can cite them.

Nothing is added that the card does not state. A card's "(+N more)" stays as
the card's own sign that its child list is cut. There are no coverage lines:
cards do not say whether they are complete, and adding that would turn them
into graph evidence.

The evidence context (for sufficiency labels, scoring and S2-style checks)
holds exactly the IS_A links the cards state. A card's parent list counts as
complete when it names every parent the concept has in the database, and its
child list when it names every child with no "(+N more)".
"""

import re
import sqlite3
from pathlib import Path

from crawler.local_client import split_fsn
from evaluation.conditions import Context

SYSTEMS = {"L-RAG": "lexical", "D-RAG": "dense"}
DEFAULT_K = 10
CARD_FORMAT = "cards"
TEXT_RETRIEVAL_VERSION = "2"  # 2: entity-name queries (1: whole question, never used in a run)

_FIELD = re.compile(r"^([A-Za-z][A-Za-z \-]*?):\s?(.*)$")
_MORE = re.compile(r"\s*\(\+(\d+) more\)\s*$")
_WORD = re.compile(r"[\w]+", re.UNICODE)

CARDS_HEADER = (
    "Retrieved SNOMED CT concept cards (text search). Each IS_A link a card states has an edge ID: "
    "after a parent concept, the card's concept IS_A that parent; after an included concept, that "
    "concept IS_A the card's concept; in the Hierarchy line, each concept IS_A the one before it. "
    "Cite edges by their IDs."
)


# ----------------------------------------------------------------------
# cards
# ----------------------------------------------------------------------

def parse_card(text):
    """
    The card's fields in order: [(field, value), ...]. Lines without a
    "Field: value" shape are appended to the previous field's value.
    """
    fields = []
    for line in (text or "").splitlines():
        match = _FIELD.match(line)
        if match:
            fields.append([match.group(1), match.group(2)])
        elif fields:
            fields[-1][1] += "\n" + line
        else:
            fields.append(["", line])
    return [tuple(f) for f in fields]


def split_list(value):
    """('a; b; c', ) -> (['a', 'b', 'c'], n_more) for a 'Parent concepts' or 'Includes' value."""
    more = 0
    match = _MORE.search(value or "")
    if match:
        more = int(match.group(1))
        value = value[:match.start()]
    names = [n.strip() for n in (value or "").split(";") if n.strip()]
    return names, more


class CardStore:
    """Read-only access to cards, names and IS_A links in snomed.db (SQL lookups, no graph in memory)."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.con = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        self._labels = {}

    def close(self):
        self.con.close()

    def card(self, concept_id):
        row = self.con.execute("SELECT card_text FROM concepts WHERE concept_id = ?", (concept_id,)).fetchone()
        return row[0] if row else None

    def label(self, concept_id):
        if concept_id not in self._labels:
            row = self.con.execute("SELECT term FROM concepts WHERE concept_id = ?", (concept_id,)).fetchone()
            self._labels[concept_id] = (split_fsn(row[0])[0] if row else None) or concept_id
        return self._labels[concept_id]

    def parents(self, concept_id):
        return sorted(p for (p,) in self.con.execute("SELECT parent_id FROM edges WHERE child_id = ?", (concept_id,)))

    def children(self, concept_id):
        return sorted(c for (c,) in self.con.execute("SELECT child_id FROM edges WHERE parent_id = ?", (concept_id,)))

    def lexical_search(self, query, k, keep=None):
        """BM25 over concepts_fts: any query word may match (OR), best score first; only IDs in ``keep``."""
        words = sorted({w.lower() for w in _WORD.findall(query or "")})
        if not words:
            return []
        expression = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
        found, offset, page = [], 0, max(50, k * 20)
        while len(found) < k:
            rows = self.con.execute(
                "SELECT concept_id FROM concepts_fts WHERE concepts_fts MATCH ? "
                "ORDER BY bm25(concepts_fts), concept_id LIMIT ? OFFSET ?", (expression, page, offset)).fetchall()
            for (concept_id,) in rows:
                if (keep is None or concept_id in keep) and concept_id not in found:
                    found.append(concept_id)
                    if len(found) == k:
                        break
            if len(rows) < page:
                break
            offset += page
        return found


def _match(names, candidates, label_of):
    """Map each name to the candidate with that label: [(name, id or None)], plus unmatched candidates."""
    by_label = {}
    for candidate in candidates:
        by_label.setdefault(label_of(candidate), []).append(candidate)
    mapped = []
    for name in names:
        options = by_label.get(name) or []
        mapped.append((name, options.pop(0) if options else None))
    return mapped


def link_card(concept_id, text, store):
    """
    A parsed card with every named parent, child and path concept matched to
    its ID. Returns {"fields": [...], "parents": [(name, id)], "children":
    [(name, id)], "more": n, "path": [(name, id)], "edges": {(child, parent)},
    "unmatched": n}.
    """
    fields = parse_card(text)
    values = dict(fields)
    parent_names, _ = split_list(values.get("Parent concepts", ""))
    child_names, more = split_list(values.get("Includes", ""))
    parents = _match(parent_names, store.parents(concept_id), store.label)
    children = _match(child_names, store.children(concept_id), store.label)

    path_names = [n.strip() for n in values.get("Hierarchy", "").split(" > ") if n.strip()]
    path = [(n, None) for n in path_names]
    if path_names and path_names[-1] == store.label(concept_id):
        path[-1] = (path_names[-1], concept_id)
        current = concept_id
        for i in range(len(path_names) - 2, -1, -1):          # walk up the stated path
            nxt = _match([path_names[i]], store.parents(current), store.label)[0][1]
            if nxt is None:
                break
            path[i] = (path_names[i], nxt)
            current = nxt

    edges = {(concept_id, p) for _, p in parents if p}
    edges |= {(c, concept_id) for _, c in children if c}
    edges |= {(path[i + 1][1], path[i][1]) for i in range(len(path) - 1) if path[i][1] and path[i + 1][1]}
    unmatched = sum(1 for _, i in parents + children + path if i is None)
    return {"fields": fields, "parents": parents, "children": children, "more": more, "path": path,
            "edges": edges, "unmatched": unmatched}


# ----------------------------------------------------------------------
# evidence
# ----------------------------------------------------------------------

class CardContext(Context):
    """A Context whose evidence is the retrieved cards (render format ``cards``)."""

    cards: list = None          # [(concept_id, linked card)] in rank order

    def render(self, fmt=CARD_FORMAT):
        if fmt != CARD_FORMAT:
            return super().render(fmt)
        if not self.cards:
            return "No evidence was retrieved."
        eid = {edge: e for e, edge in self.edge_index().items()}

        def ref(name, concept_id, edge=None):
            text = f"{name} [{concept_id}]" if concept_id else name
            return f"{text} ({eid[edge]})" if edge in eid else text

        lines = [CARDS_HEADER]
        for rank, (concept_id, card) in enumerate(self.cards, 1):
            lines.append("")
            lines.append(f"Card {rank}: {self.nodes.get(concept_id, concept_id)} [{concept_id}]")
            for field, value in card["fields"]:
                if field == "Term":
                    continue
                if field == "Parent concepts":
                    value = "; ".join(ref(n, p, (concept_id, p)) for n, p in card["parents"])
                elif field == "Includes":
                    value = "; ".join(ref(n, c, (c, concept_id)) for n, c in card["children"])
                    if card["more"]:
                        value += f" (+{card['more']} more)"
                elif field == "Hierarchy":
                    steps = card["path"]
                    value = " > ".join(ref(n, i, (i, steps[k - 1][1]) if k else None)
                                       for k, (n, i) in enumerate(steps))
                lines.append(f"{field}: {value}" if field else value)
        return "\n".join(lines)


def card_context(item, card_ids, store, system):
    """The evidence context for ``item`` from the cards of ``card_ids`` (rank order)."""
    context = CardContext(condition=system, focal_id=item.concept_id)
    context.cards = []
    for concept_id in card_ids:
        text = store.card(concept_id)
        if text is None:
            continue
        card = link_card(concept_id, text, store)
        context.cards.append((concept_id, card))
        context.edges |= card["edges"]
        for _, cid in card["parents"] + card["children"] + card["path"]:
            if cid:
                context.nodes[cid] = store.label(cid)
        context.nodes[concept_id] = store.label(concept_id)
        all_parents, all_children = store.parents(concept_id), store.children(concept_id)
        named_parents = {p for _, p in card["parents"] if p}
        named_children = {c for _, c in card["children"] if c}
        context.parents_status[concept_id] = "complete" if named_parents == set(all_parents) else "partial"
        context.children_status[concept_id] = ("complete" if not card["more"] and named_children == set(all_children)
                                               else "partial")
        context.children_total[concept_id] = len(named_children) + card["more"]
    context.requests_used = len(card_ids)
    return context


# ----------------------------------------------------------------------
# queries and search
# ----------------------------------------------------------------------

TWO_CONCEPT_TASKS = ("T3", "T4", "T6")


def query_concepts(item):
    """The concepts a question is about: the concept, plus the target for T3, T4 and T6."""
    if item.task in TWO_CONCEPT_TASKS and item.target_id:
        return [item.concept_id, item.target_id]
    return [item.concept_id]


def entity_queries(item, label_of):
    """One text query per concept: its name (what a text index can match; IDs cannot be)."""
    return [label_of(c) for c in query_concepts(item)]


def share_k(k, n_queries):
    """How many cards each query gets: k split as evenly as possible, earlier queries first."""
    base, extra = divmod(k, n_queries)
    return [base + (1 if i < extra else 0) for i in range(n_queries)]


def merge_rankings(rankings, quotas, k):
    """
    Interleave per-query rankings (rank 1 of each, then rank 2, ...), each
    query contributing up to its quota, skipping duplicates; if a query runs
    short, the others fill the remaining places.
    """
    merged, taken = [], [0] * len(rankings)
    pointers = [0] * len(rankings)
    for allow_extra in (False, True):
        progress = True
        while len(merged) < k and progress:
            progress = False
            for q, ranking in enumerate(rankings):
                if len(merged) >= k or (not allow_extra and taken[q] >= quotas[q]):
                    continue
                while pointers[q] < len(ranking) and ranking[pointers[q]] in merged:
                    pointers[q] += 1
                if pointers[q] < len(ranking):
                    merged.append(ranking[pointers[q]])
                    pointers[q] += 1
                    taken[q] += 1
                    progress = True
    return merged


def lexical_retrieve(item, store, label_of, k=DEFAULT_K, keep=None):
    """(card IDs, queries) for L-RAG: BM25 per concept name, k shared between the names."""
    queries = entity_queries(item, label_of)
    quotas = share_k(k, len(queries))
    rankings = [store.lexical_search(q, k, keep=keep) for q in queries]
    return merge_rankings(rankings, quotas, k), queries


def dense_search(query_vector, matrix, ids, k=DEFAULT_K):
    """
    Top-``k`` IDs by cosine similarity. ``matrix`` holds unit-length card
    vectors (rows aligned with ``ids``); ``query_vector`` is a unit-length
    BGE-M3 question vector. Ties break by ID.
    """
    import numpy as np
    scores = matrix @ np.asarray(query_vector, dtype=matrix.dtype)
    k = min(k, len(ids))
    top = np.argpartition(-scores, k - 1)[:k] if k < len(ids) else np.arange(len(ids))
    return [ids[i] for i in sorted(top, key=lambda i: (-float(scores[i]), ids[i]))]


def retrieval_record(item, system, card_ids, queries, context):
    """What one text retrieval did, for the results row and the protocol."""
    return {
        "text_retrieval_version": TEXT_RETRIEVAL_VERSION,
        "system": system,
        "item_id": item.item_id,
        "queries": list(queries),
        "cards": list(card_ids),
        "focal_card_rank": (card_ids.index(item.concept_id) + 1) if item.concept_id in card_ids else None,
        "target_card_rank": ((card_ids.index(item.target_id) + 1)
                             if item.target_id and item.target_id in card_ids else None),
        "edges": len(context.edges),
        "unmatched_names": sum(card["unmatched"] for _, card in context.cards),
    }

