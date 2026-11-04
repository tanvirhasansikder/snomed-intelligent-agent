"""answer_in_evidence vs sufficient (evaluation-only label, protocol v2.0 Amendment 1)."""

from evaluation.conditions import Context
from evaluation.tasks import Item, answer_in_evidence, sufficient

# X(700) -> P(500) -> L(400) -> TOP(100); Z(710) -> Q(510) -> L(400)
EDGES = {("700", "500"), ("500", "400"), ("400", "100"), ("710", "510"), ("510", "400")}
NODES = {n: n for n in ("700", "500", "400", "100", "710", "510")}


def ctx(edges=EDGES, complete=("700", "500", "400", "100", "710", "510"), condition="U2", children=None):
    return Context(condition=condition, focal_id="700", nodes=dict(NODES), edges=set(edges),
                   parents_status={c: "complete" for c in complete}, children_status=children or {})


def test_t6_answer_in_window_but_not_certified():
    # L reached by both within depth 2, but L's parents were not retrieved: not certifiable
    window = ctx(edges=EDGES - {("400", "100")}, complete=("700", "500", "710", "510"))
    item = Item("t6", "T6", "700", ["400"], target_id="710")
    assert answer_in_evidence(item, window) and not sufficient(item, window)
    assert answer_in_evidence(item, ctx()) and sufficient(item, ctx())


def test_t6_wrong_window_is_neither():
    partial = ctx(edges={("700", "500"), ("710", "510")}, complete=("700", "710"))
    item = Item("t6", "T6", "700", ["400"], target_id="710")
    assert not answer_in_evidence(item, partial) and not sufficient(item, partial)


def test_t1_and_t2_contain_answer():
    assert answer_in_evidence(Item("t1", "T1", "700", ["500"]), ctx())
    assert not answer_in_evidence(Item("t1", "T1", "700", ["500", "999"]), ctx())
    leaf = ctx(children={"700": "complete"})
    assert answer_in_evidence(Item("t2", "T2", "700", []), leaf)          # confirmed leaf
    assert not answer_in_evidence(Item("t2", "T2", "700", []), ctx())     # children never retrieved


def test_other_tasks_and_closed_book_follow_sufficiency():
    neg = Item("t3", "T3", "700", False, target_id="510", polarity="neg")
    assert answer_in_evidence(neg, ctx()) == sufficient(neg, ctx())
    assert not answer_in_evidence(Item("t1", "T1", "700", ["500"]), ctx(condition="C0"))
