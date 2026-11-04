"""
Parsing and normalisation of model answers.

``parse_answer`` accepts the raw model text and returns a dict with
``answer``, ``claims`` (list of dicts), ``insufficient_evidence`` and
``parse_error``. It tolerates code fences and text around the JSON object;
anything it cannot interpret is reported, never guessed.
"""

import json
import re

from explanation.explainer import BINARY_RELATIONS, RELATIONS

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_ID = re.compile(r"\d{6,18}")
_EDGE_ID = re.compile(r"E\d+")


def _as_citations(raw):
    """Clean a claim's ``cites`` list: edge IDs such as "E3", deduplicated, in order.
    Returns (citations, number of entries dropped as malformed)."""
    if raw is None:
        return [], 0
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return [], 1
    cites, dropped = [], 0
    for entry in raw:
        text = str(entry).strip().upper() if isinstance(entry, (str, int)) and not isinstance(entry, bool) else ""
        if _EDGE_ID.fullmatch(text):
            if text not in cites:
                cites.append(text)
        else:
            dropped += 1
    return cites, dropped


def _extract_json(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty response")
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for candidate in candidates:
        candidate = candidate.strip()
        start = candidate.find("{")
        while start != -1:
            depth, in_string, escape = 0, False, False
            for end in range(start, len(candidate)):
                ch = candidate[end]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                elif ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            value = json.loads(candidate[start:end + 1])
                            if isinstance(value, dict):
                                return value
                        except json.JSONDecodeError:
                            pass
                        break
            start = candidate.find("{", start + 1)
    raise ValueError("no JSON object found")


def _as_id(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        value = str(value)
    if isinstance(value, str):
        match = _ID.search(value)
        return match.group(0) if match else None
    return None


def _normalise_claims(raw):
    """Returns (claims, claims dropped, citation entries dropped)."""
    claims, dropped, bad_cites = [], 0, 0
    if not isinstance(raw, list):
        return claims, (1 if raw not in (None, []) else 0), 0
    for entry in raw:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        relation = str(entry.get("relation", "")).strip().lower()
        subject = _as_id(entry.get("subject"))
        obj = _as_id(entry.get("object"))
        source = str(entry.get("source", "unspecified")).strip().lower()
        if source not in ("evidence", "model_knowledge"):
            source = "unspecified"
        if relation not in RELATIONS or subject is None or (relation in BINARY_RELATIONS and obj is None):
            dropped += 1
            continue
        cites, bad = _as_citations(entry.get("cites"))
        bad_cites += bad
        claims.append({"subject": subject, "relation": relation,
                       "object": obj if relation in BINARY_RELATIONS else None, "source": source,
                       "cites": cites})
    return claims, dropped, bad_cites


def parse_answer(text, task):
    result = {"answer": None, "claims": [], "insufficient_evidence": False,
              "parse_error": None, "dropped_claims": 0, "dropped_citations": 0}
    try:
        data = _extract_json(text)
    except ValueError as exc:
        result["parse_error"] = str(exc)
        return result

    flag = data.get("insufficient_evidence", False)
    result["insufficient_evidence"] = flag is True or (isinstance(flag, str) and flag.lower() == "true")
    result["claims"], result["dropped_claims"], result["dropped_citations"] = _normalise_claims(data.get("claims"))

    answer = data.get("answer")
    if task in ("T1", "T2", "T4", "T6"):
        if isinstance(answer, list):
            ids = [_as_id(a) for a in answer]
            result["answer"] = [i for i in ids if i is not None]
        elif answer in (None, "", "unknown"):
            result["answer"] = []
        else:
            result["parse_error"] = "answer is not a list"
            result["answer"] = []
    elif task == "T3":
        value = str(answer).strip().lower() if answer is not None else "unknown"
        result["answer"] = value if value in ("yes", "no", "unknown") else "unknown"
        if value not in ("yes", "no", "unknown"):
            result["parse_error"] = "answer is not yes/no/unknown"
    else:
        result["answer"] = answer if isinstance(answer, str) else ("" if answer is None else json.dumps(answer))
    return result
