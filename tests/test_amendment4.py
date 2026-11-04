"""
Protocol Amendment 4 (pilot review, 2026-11-05):
T2 asks about the descent parent, and the system runner interleaves by item.
"""

from evaluation.system_runner import LLM_SYSTEMS, SystemExperiment
from evaluation.tasks import t2_subject
from tests.test_system_runner import data  # noqa: F401  (pytest fixture)


def test_t2_asks_about_the_descent_parent(data):  # noqa: F811
    experiment = SystemExperiment(data)
    gold = experiment.gold
    t2 = [i for i in experiment.items if i.task == "T2"]
    assert t2
    subjects = [i.concept_id for i in t2]
    assert len(subjects) == len(set(subjects))              # one question per parent
    for item in t2:
        sampled = item.meta["sampled_concept"]
        assert item.concept_id == t2_subject(gold.samples[sampled])
        assert item.concept_id in gold.parents[sampled]       # a direct parent of the sampled concept
        assert sampled in item.gold                           # so the answer is never empty
        assert item.gold == sorted(gold.children[item.concept_id])
        assert item.meta["depth_min"] == gold.depth(item.concept_id)[0]


def test_t2_subject_needs_a_walk_of_at_least_one_step():
    assert t2_subject({"descent_path": ["TOP"]}) is None
    assert t2_subject({}) is None
    assert t2_subject({"descent_path": ["TOP", "A", "B"]}) == "A"


def test_runner_interleaves_systems_by_item(data):  # noqa: F811
    experiment = SystemExperiment(data)
    plan = experiment.plan_systems(data["models"])
    n = len(LLM_SYSTEMS)
    first = plan[:n]
    assert [system for _, system, _, _ in first] == list(LLM_SYSTEMS)
    assert len({(item.item_id, rep) for _, _, item, rep in first}) == 1
    # S1 always comes right before S2 for the same item, so S2 can reuse S1's call
    for k, (_, system, item, rep) in enumerate(plan):
        if system == "S2":
            assert plan[k - 1][1] == "S1" and plan[k - 1][2].item_id == item.item_id and plan[k - 1][3] == rep
