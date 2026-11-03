"""
Tests for the research pipeline (evaluation/, llm/, database/memory_repository).

All data here is SYNTHETIC (evaluation/synthetic.py or hand-written graphs).
Expected values are derived by hand from the graph pictures in the comments,
or from the SyntheticOntology's own parent table, never from the code under test.
"""

import json
from pathlib import Path

import pytest

from database.memory_repository import InMemoryGraphRepository
from evaluation import stats
from evaluation.bioportal_cache import CallStore, RecordingClient, SnapshotClient, SnapshotMissError
from evaluation.collect import collect, sample_concepts, validate_hierarchies
from evaluation.conditions import Context, build_context
from evaluation.gold import GoldIndex
from evaluation.scoring import score_answer
from evaluation.synthetic import ROOT_ID, SyntheticOntology
from evaluation.tasks import Item, generate_items, reference_answer, sufficient
from explanation.explainer import (
    CONTRADICTED,
    FALSE_IN_REFERENCE,
    INSUFFICIENT,
    SUPPORTED,
    TRUE_NOT_RETRIEVED,
    HierarchyClaim,
    ReferenceHierarchy,
    SubgraphEvidence,
    parse_claims,
    verify_claim,
)
from llm.client import CachedClient, LLMConfigurationError, LLMError, OpenAICompatibleClient, ResponseCache
from llm.prompt_manager import PROMPT_VERSION, SYSTEM_PROMPT, build_messages
from llm.reasoner import parse_answer
from tests.fakes import FakeResponse


# ----------------------------------------------------------------------
# Shared synthetic world
# ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def ontology():
    return SyntheticOntology(seed=3)


@pytest.fixture(scope="module")
def world(ontology, tmp_path_factory):
    """A collected synthetic data set: store, dataset, gold index."""
    tmp = tmp_path_factory.mktemp("world")
    config = {
        "name": "test", "synthetic": True,
        "cache_path": str(tmp / "calls.jsonl"), "dataset_path": str(tmp / "dataset.json"),
        "data": {"per_hierarchy": 4, "seed": 5, "min_depth": 2, "max_depth": 6,
                 "hierarchies": ontology.top_level_names()},
    }
    store = CallStore(config["cache_path"])
    dataset = collect(config, ontology, store)
    return {"store": store, "dataset": dataset, "gold": GoldIndex(store, dataset), "config": config}


def true_ancestors(ontology, concept_id):
    seen, stack = set(), list(ontology.parents[concept_id])
    while stack:
        node = stack.pop()
        if node not in seen:
            seen.add(node)
            stack.extend(ontology.parents[node])
    return seen


# ----------------------------------------------------------------------
# In-memory repository
# ----------------------------------------------------------------------

def test_memory_repository_matches_repository_semantics():
    repo = InMemoryGraphRepository()
    repo.save_concept("1001000", "child")
    repo.save_concept("1002007", "parent")

    assert repo.save_relationship("1001000", "1002007") is True
    assert repo.save_relationship("1001000", "9999999") is False
    assert [p["concept_id"] for p in repo.get_parents("1001000")] == ["1002007"]
    assert [c["concept_id"] for c in repo.get_children("1002007")] == ["1001000"]
    assert repo.get_coverage("1001000") == {"parents": None, "children": None}
    assert repo.mark_expansion("1001000", "up", "complete", count=1) is True
    assert repo.get_expansion_status("1001000", "up")["status"] == "complete"
    with pytest.raises(ValueError):
        repo.mark_expansion("1001000", "sideways", "complete")
    with pytest.raises(ValueError):
        repo.clear_database()


# ----------------------------------------------------------------------
# Record / replay
# ----------------------------------------------------------------------

def test_call_store_persists_and_reloads(tmp_path):
    store = CallStore(tmp_path / "calls.jsonl")
    store.put("get_parents", "1001000", [{"concept_id": "1002007", "name": "P"}])

    reloaded = CallStore(tmp_path / "calls.jsonl")

    assert reloaded.get("get_parents", "1001000") == [{"concept_id": "1002007", "name": "P"}]
    assert ("get_parents", "1001000") in reloaded


def test_recording_client_caches_and_does_not_store_failures(tmp_path, ontology):
    store = CallStore(tmp_path / "calls.jsonl")
    client = RecordingClient(ontology, store)
    some = ontology.top_level[0]

    first = client.get_parents(some)
    before = ontology.request_count
    second = client.get_parents(some)

    assert first == second == [{"concept_id": ROOT_ID, "name": "Synthetic root"}]
    assert ontology.request_count == before
    with pytest.raises(Exception):
        client.get_parents("9999999")
    assert ("get_parents", "9999999") not in store


def test_snapshot_client_raises_on_missing_call(tmp_path):
    client = SnapshotClient(CallStore(tmp_path / "empty.jsonl"))
    with pytest.raises(SnapshotMissError):
        client.get_parents("1001000", raise_errors=True)
    assert client.get_parents("1001000") == []


def test_snapshot_client_applies_page_cap(tmp_path):
    store = CallStore(tmp_path / "calls.jsonl")
    kids = [{"concept_id": str(1000000 + i), "name": f"c{i}"} for i in range(250)]
    store.put("fetch_children", "1001000", {"children": kids, "complete": True, "pages_fetched": 3})

    capped = SnapshotClient(store).fetch_children("1001000", max_pages=2, pagesize=100)

    assert len(capped["children"]) == 200
    assert capped["complete"] is False


# ----------------------------------------------------------------------
# Sampling and closure
# ----------------------------------------------------------------------

def test_sampler_is_deterministic_and_respects_exclusions(tmp_path, ontology):
    hierarchies = ontology.top_level_names()

    def run(exclude=()):
        client = RecordingClient(ontology, CallStore(tmp_path / f"s{len(exclude)}.jsonl"))
        return sample_concepts(client, hierarchies, 3, seed=9, min_depth=2, max_depth=5, exclude=exclude)

    first, second = run(), run()
    assert [s["concept_id"] for s in first] == [s["concept_id"] for s in second]
    assert len({s["concept_id"] for s in first}) == len(first)
    excluded = {first[0]["concept_id"]}
    assert first[0]["concept_id"] not in {s["concept_id"] for s in run(exclude=excluded)}
    for s in first:
        assert s["descent_path"][0] == s["hierarchy_id"]
        assert s["concept_id"] == s["descent_path"][-1]
        for child, parent in zip(s["descent_path"][1:], s["descent_path"]):
            assert parent in ontology.parents[child]


def test_validate_hierarchies_rejects_non_top_level(tmp_path, ontology):
    client = RecordingClient(ontology, CallStore(tmp_path / "v.jsonl"))
    deep = next(c for c, ps in ontology.parents.items() if ps and ps[0] in ontology.top_level)
    with pytest.raises(ValueError):
        validate_hierarchies(client, [deep])


def test_closure_matches_true_ancestors(world, ontology):
    gold = world["gold"]
    for sample in world["dataset"]["samples"]:
        x = sample["concept_id"]
        assert sample["ancestors_consistent"] is True
        assert set(gold.ancestors(x)) == true_ancestors(ontology, x)
        assert set(gold.parents[x]) == set(ontology.parents[x])


def test_gold_depth_is_shortest_and_longest_path(world, ontology):
    gold = world["gold"]
    top = ontology.top_level[0]
    assert gold.depth(top) == (1, 1)
    for sample in world["dataset"]["samples"]:
        shortest, longest = gold.depth(sample["concept_id"])
        assert 1 <= shortest <= longest


# ----------------------------------------------------------------------
# Retrieval conditions (real crawler on replayed data)
# ----------------------------------------------------------------------

def deepest_sample(world):
    return max(world["dataset"]["samples"], key=lambda s: world["gold"].depth(s["concept_id"])[0])["concept_id"]


def test_conditions_produce_expected_coverage(world, ontology):
    snapshot = SnapshotClient(world["store"])
    x = deepest_sample(world)
    parents = set(ontology.parents[x])

    c0 = build_context(snapshot, x, "C0")
    u1 = build_context(snapshot, x, "U1")
    u2 = build_context(snapshot, x, "U2")
    u2d = build_context(snapshot, x, "U2D")
    full = build_context(snapshot, x, "FULL")

    assert c0.nodes == {} and c0.render("plain") == "No evidence was retrieved."
    assert set(u1.parents_of(x)) == parents and u1.parents_complete(x)
    assert u1.frontier == parents
    assert u1.ancestors_of(x) == parents
    assert set(u2.ancestors_of(x)) <= true_ancestors(ontology, x)
    assert not u2.closure_complete(x)
    assert x not in u2.children_status
    assert u2d.children_complete(x) and set(u2d.children_of(x)) == set(ontology.children.get(x, []))
    assert full.closure_complete(x)
    assert full.ancestors_of(x) == true_ancestors(ontology, x)


def test_plain_and_annotated_contain_identical_edges(world):
    snapshot = SnapshotClient(world["store"])
    context = build_context(snapshot, deepest_sample(world), "U2")
    plain, annotated = context.render("plain"), context.render("annotated")

    assert annotated.startswith(plain)
    assert "Retrieval coverage" in annotated and "Retrieval coverage" not in plain
    assert "NOT RETRIEVED" in annotated
    assert "Retrieval stopped at the depth limit" in annotated


def test_child_display_cap_marks_list_partial(tmp_path):
    store = CallStore(tmp_path / "calls.jsonl")
    x = "1001000"
    kids = [{"concept_id": str(2000000 + i), "name": f"kid {i:03d}"} for i in range(12)]
    store.put("get_concept", x, {"concept_id": x, "name": "X"})
    store.put("get_parents", x, [])
    store.put("fetch_children", x, {"children": kids, "complete": True, "pages_fetched": 1})

    context = build_context(SnapshotClient(store), x, "U2D", child_display_cap=5)

    assert len(context.children_of(x)) == 5
    assert context.children_status[x] == "partial"
    assert not context.children_complete(x)
    assert "PARTIAL (5 listed; at least 12 retrieved" in context.render("annotated")


# ----------------------------------------------------------------------
# Items, sufficiency, R0
# ----------------------------------------------------------------------

def test_items_have_valid_gold(world, ontology):
    gold = world["gold"]
    items = generate_items(gold, seed=0)
    assert items == generate_items(gold, seed=0)
    for item in items:
        x = item.concept_id
        if item.task == "T1":
            assert set(item.gold) == set(ontology.parents[x])
        elif item.task == "T2":
            assert set(item.gold) == set(ontology.children.get(x, []))
        elif item.task == "T3" and item.polarity == "pos":
            assert item.target_id in true_ancestors(ontology, x) and item.target_id != ROOT_ID
            assert item.target_id not in ontology.parents[x]
        elif item.task == "T3":
            assert item.target_id not in true_ancestors(ontology, x) and item.target_id != x


def test_reference_system_is_perfect_under_full_retrieval(world):
    gold, snapshot = world["gold"], SnapshotClient(world["store"])
    reference = gold.reference()
    for item in generate_items(gold, seed=0):
        if item.task == "T5":
            continue
        context = build_context(snapshot, item.concept_id, "FULL")
        assert sufficient(item, context), item.item_id
        parsed = dict(reference_answer(item, context), parse_error=None, dropped_claims=0)
        row = score_answer(item, parsed, context, reference)
        assert row["score"] == 1.0, item.item_id
        assert row["n_misattributed"] == 0


def test_reference_system_abstains_when_insufficient(world):
    gold, snapshot = world["gold"], SnapshotClient(world["store"])
    item = next(i for i in generate_items(gold, seed=0) if i.task == "T3" and i.polarity == "neg")
    context = build_context(snapshot, item.concept_id, "U1")

    assert not sufficient(item, context)
    assert reference_answer(item, context)["answer"] == "unknown"


# ----------------------------------------------------------------------
# Claim verifier: absence claims
# ----------------------------------------------------------------------

def test_absence_claims_are_supported_only_with_complete_evidence():
    leaf_known = SubgraphEvidence({"1001000": "x"}, set(), children_complete={"1001000"})
    unknown = SubgraphEvidence({"1001000": "x"}, set())
    has_child = SubgraphEvidence({"1001000": "x", "1002007": "c"}, {("1002007", "1001000")})
    claim = HierarchyClaim("1001000", "no_children")

    assert verify_claim(claim, leaf_known) == SUPPORTED
    assert verify_claim(claim, unknown) == INSUFFICIENT
    assert verify_claim(claim, has_child) == CONTRADICTED


def test_absence_claims_against_reference():
    reference = ReferenceHierarchy({("1002007", "1001000")}, nodes={"1001000", "1002007", "1003003"},
                                   children_complete={"1001000", "1003003"})
    empty = SubgraphEvidence({}, set())

    assert verify_claim(HierarchyClaim("1001000", "no_children"), empty, reference) == FALSE_IN_REFERENCE
    assert verify_claim(HierarchyClaim("1003003", "no_children"), empty, reference) == TRUE_NOT_RETRIEVED


def test_parse_claims_handles_unary_relations():
    claims = parse_claims([{"subject": "1001000", "relation": "no_children", "object": None}])
    assert claims == [HierarchyClaim("1001000", "no_children")]
    with pytest.raises(ValueError):
        HierarchyClaim("1001000", "no_children", "1002007")


# ----------------------------------------------------------------------
# Answer parsing
# ----------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    '{"answer": ["205237003"], "claims": [], "insufficient_evidence": false}',
    'Here you go:\n```json\n{"answer": ["205237003"], "claims": []}\n```',
    'Sure! {"answer": [205237003], "claims": []} Hope this helps.',
])
def test_parse_answer_tolerates_formatting(text):
    assert parse_answer(text, "T1")["answer"] == ["205237003"]


def test_parse_answer_reports_garbage():
    result = parse_answer("I think it is pneumonitis.", "T1")
    assert result["parse_error"] and result["answer"] is None


def test_parse_answer_normalises_claims_and_drops_invalid():
    text = json.dumps({"answer": "yes", "claims": [
        {"subject": "233604007", "relation": "ancestor", "object": "404684003", "source": "evidence"},
        {"subject": "233604007", "relation": "sibling_of", "object": "1"},
        {"subject": "233604007", "relation": "no_children", "object": "null", "source": "Model_Knowledge"},
        "nonsense",
    ], "insufficient_evidence": "false"})
    result = parse_answer(text, "T3")
    assert result["answer"] == "yes"
    assert [c["relation"] for c in result["claims"]] == ["ancestor", "no_children"]
    assert result["claims"][1]["source"] == "model_knowledge"
    assert result["dropped_claims"] == 2
    assert result["insufficient_evidence"] is False


# ----------------------------------------------------------------------
# LLM client and cache
# ----------------------------------------------------------------------

class PostSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


OK = FakeResponse(200, {"model": "m-1", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2}})


def test_openai_client_success_and_retry(monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test-secret-123")
    session = PostSession([FakeResponse(429, text="slow", headers={"Retry-After": "1"}), OK])
    client = OpenAICompatibleClient("p", "https://example.invalid/v1/", "m", api_key_env="TEST_LLM_KEY",
                                    json_mode=True, session=session, sleep=lambda s: None)

    response = client.complete([{"role": "user", "content": "hi"}])

    assert response.text == "{}" and response.prompt_tokens == 10
    assert session.calls[0]["url"] == "https://example.invalid/v1/chat/completions"
    assert session.calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert session.calls[0]["headers"]["Authorization"] == "Bearer sk-test-secret-123"
    assert "sk-test-secret-123" not in repr(client)
    assert "sk-test-secret-123" not in json.dumps(client.request_fingerprint())


def test_openai_client_missing_key_and_hard_errors(monkeypatch):
    monkeypatch.delenv("TEST_LLM_KEY", raising=False)
    with pytest.raises(LLMConfigurationError):
        OpenAICompatibleClient("p", "https://example.invalid/v1", "m", api_key_env="TEST_LLM_KEY")
    client = OpenAICompatibleClient("p", "https://example.invalid/v1", "m",
                                    session=PostSession([FakeResponse(401, text="no")]), sleep=lambda s: None)
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "hi"}])


def test_cached_client_never_calls_twice(tmp_path):
    session = PostSession([OK])
    client = OpenAICompatibleClient("p", "https://example.invalid/v1", "m", session=session, sleep=lambda s: None)
    cached = CachedClient(client, ResponseCache(tmp_path / "cache.jsonl"))
    messages = [{"role": "user", "content": "hi"}]

    first = cached.complete(messages)
    second = CachedClient(client, ResponseCache(tmp_path / "cache.jsonl")).complete(messages)

    assert len(session.calls) == 1
    assert first.cached is False and second.cached is True and second.text == "{}"


def test_prompt_is_versioned_and_identical_across_formats(world):
    gold, snapshot = world["gold"], SnapshotClient(world["store"])
    item = next(i for i in generate_items(gold, seed=0) if i.task == "T1")
    context = build_context(snapshot, item.concept_id, "U2")
    plain = build_messages(item, context.render("plain"), gold.label)
    annotated = build_messages(item, context.render("annotated"), gold.label)

    assert PROMPT_VERSION
    assert plain[0]["content"] == annotated[0]["content"] == SYSTEM_PROMPT
    assert plain[1]["content"] != annotated[1]["content"]
    assert item.concept_id in plain[1]["content"]


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------

def simple_context():
    """X(1001000) IS_A P(1002007); parents of X complete; children of X not retrieved."""
    return Context(condition="U1", focal_id="1001000", nodes={"1001000": "X", "1002007": "P"},
                   edges={("1001000", "1002007")}, parents_status={"1001000": "complete"})


REF = ReferenceHierarchy({("1001000", "1002007"), ("1002007", "1003003"), ("1004009", "1001000")},
                         nodes={"1001000", "1002007", "1003003", "1004009"}, children_complete={"1001000"},
                         parents_complete={"1001000", "1002007", "1003003"})


def parsed(answer, claims, insufficient=False):
    return {"answer": answer, "claims": claims, "insufficient_evidence": insufficient,
            "parse_error": None, "dropped_claims": 0}


def test_scoring_counts_misattribution_and_fill_in():
    item = Item("T1:1001000", "T1", "1001000", ["1002007"])
    row = score_answer(item, parsed(["1002007"], [
        {"subject": "1001000", "relation": "direct_parent", "object": "1002007", "source": "evidence"},
        {"subject": "1001000", "relation": "ancestor", "object": "1003003", "source": "evidence"},
        {"subject": "1001000", "relation": "ancestor", "object": "1003003", "source": "model_knowledge"},
    ]), simple_context(), REF)

    assert row["score"] == 1.0
    assert row["n_supported_by_subgraph"] == 1 and row["n_true_not_retrieved"] == 2
    assert row["n_misattributed"] == 1
    assert row["n_model_knowledge_true"] == 1


def test_scoring_flags_false_absence_and_scores_abstention_zero():
    item = Item("T2:1001000", "T2", "1001000", ["1004009"])
    false_leaf = score_answer(item, parsed([], [
        {"subject": "1001000", "relation": "no_children", "object": None, "source": "evidence"}]),
        simple_context(), REF)
    abstain = score_answer(item, parsed([], [], insufficient=True), simple_context(), REF)

    assert false_leaf["n_false_absence"] == 1 and false_leaf["score"] == 0.0
    assert abstain["abstained"] is True and abstain["score"] == 0.0


def test_abstention_on_true_leaf_is_not_rewarded():
    leaf_item = Item("T2:1004009", "T2", "1004009", [])
    context = Context(condition="U1", focal_id="1004009", nodes={"1004009": "L"})
    assert score_answer(leaf_item, parsed([], [], insufficient=True), context, REF)["score"] == 0.0
    assert score_answer(leaf_item, parsed([], []), context, REF)["score"] == 1.0


def test_verified_view_keeps_only_evidence():
    item = Item("T3+:1001000", "T3", "1001000", True, target_id="1003003", polarity="pos")
    row = score_answer(item, parsed("yes", []), simple_context(), REF)
    assert row["score"] == 1.0
    assert row["v_score"] == 0.0 and row["v_abstained"] is True


# ----------------------------------------------------------------------
# Statistics (reference values computed with SciPy 1.x on the same inputs)
# ----------------------------------------------------------------------

def test_mcnemar_exact_matches_binomial():
    pairs = [(True, False)] * 2 + [(False, True)] * 10 + [(True, True)] * 30
    result = stats.mcnemar_exact(pairs)
    assert (result["b10"], result["b01"]) == (2, 10)
    assert result["p"] == pytest.approx(0.0385742, abs=1e-6)


def test_wilcoxon_signed_rank_known_value():
    pairs = [(0, d) for d in (1, 2, 3, 4, 5, -1.5)]
    result = stats.wilcoxon_signed_rank(pairs)
    assert result["w_plus"] == 19.0 and result["w_minus"] == 2.0
    assert result["p"] == pytest.approx(0.0934925, abs=1e-6)


def test_holm_and_kappa():
    assert stats.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert stats.cohen_kappa(list("aabbab"), list("abbbab")) == pytest.approx(2 / 3)


def test_cochran_armitage_equals_n_r_squared():
    result = stats.cochran_armitage([2, 5, 9, 14], [30, 30, 30, 30])
    assert result["z"] ** 2 == pytest.approx(14.2222, abs=1e-3)


# ----------------------------------------------------------------------
# End to end (synthetic data, MockLLM)
# ----------------------------------------------------------------------

def test_end_to_end_dry_run(world, tmp_path):
    from evaluation import report
    from evaluation.runner import Experiment
    from llm.client import ResponseCache as Cache

    config = dict(world["config"])
    config.update({
        "experiment": {"tasks": ["T1", "T3", "T5"], "conditions": ["C0", "U1", "FULL"],
                       "formats": ["plain", "annotated"], "item_seed": 0},
        "models": [{"name": "mock", "provider": "mock", "model": "mock-1"}],
        "results_dir": str(tmp_path / "results"),
        "llm_cache_path": str(tmp_path / "results" / "llm.jsonl"),
    })
    Path(config["dataset_path"]).write_text(json.dumps(world["dataset"]), encoding="utf-8")

    experiment = Experiment(config)
    r0 = experiment.run_reference()
    made = experiment.run_models(config["models"], Cache(config["llm_cache_path"]))
    assert r0 > 0 and made > 0
    assert Experiment(config).plan(config["models"]) == []

    rows = report.load_rows(experiment.responses_path)
    summary = report.summarise(rows)
    full_r0 = [s for s in summary if s["model"] == "R0" and s["condition"] == "FULL" and s["task"] in ("T1", "T3")]
    assert full_r0 and all(s["score"] == 1.0 for s in full_r0)
    tests = report.hypothesis_tests(rows)
    assert all(0.0 <= t["p_holm"] <= 1.0 for t in tests)
    assert report.latex_table(summary).startswith("\\begin{tabular}")


# ----------------------------------------------------------------------
# Provider pinning and budget control
# ----------------------------------------------------------------------

def test_extra_body_is_sent_and_fingerprinted():
    pin = {"provider": {"order": ["deepinfra"], "allow_fallbacks": False}}
    served = FakeResponse(200, {"model": "m-1", "provider": "DeepInfra",
                                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.0001}})
    session = PostSession([served])
    client = OpenAICompatibleClient("p", "https://example.invalid/v1", "m", extra_body=pin,
                                    session=session, sleep=lambda s: None)
    plain = OpenAICompatibleClient("p", "https://example.invalid/v1", "m")

    response = client.complete([{"role": "user", "content": "hi"}])

    assert session.calls[0]["json"]["provider"] == pin["provider"]
    assert response.served_by == "DeepInfra" and response.cost_usd == pytest.approx(0.0001)
    assert client.request_fingerprint()["extra_body"] == pin
    assert "extra_body" not in plain.request_fingerprint()  # old cache keys unchanged
    with pytest.raises(LLMConfigurationError):
        OpenAICompatibleClient("p", "https://example.invalid/v1", "m", extra_body={"model": "other"})


def test_call_cost_prefers_provider_figure():
    from evaluation.runner import call_cost
    from llm.client import LLMResponse

    spec = {"price_per_mtok": [1.0, 2.0]}
    assert call_cost(spec, LLMResponse("x", "m", 1000, 500)) == pytest.approx(0.002)
    assert call_cost(spec, LLMResponse("x", "m", 1000, 500, cost_usd=0.5)) == 0.5
    assert call_cost({}, LLMResponse("x", "m", 1000, 500)) is None


def test_max_cost_stops_the_run(world, tmp_path):
    from evaluation.runner import Experiment
    from llm.client import ResponseCache as Cache

    config = dict(world["config"])
    model = {"name": "mock", "provider": "mock", "model": "mock-1", "price_per_mtok": [1000.0, 1000.0]}
    config.update({
        "experiment": {"tasks": ["T1"], "conditions": ["C0", "FULL"], "formats": ["plain"], "item_seed": 0},
        "models": [model], "results_dir": str(tmp_path / "results"),
        "llm_cache_path": str(tmp_path / "results" / "llm.jsonl"),
    })
    Path(config["dataset_path"]).write_text(json.dumps(world["dataset"]), encoding="utf-8")

    experiment = Experiment(config)
    assert len(experiment.plan([model])) > 1
    made = experiment.run_models([model], Cache(config["llm_cache_path"]), max_cost=0.000001)
    assert made == 1 and experiment.spent_usd > 0

    estimate = experiment.estimate([model])["mock"]
    assert estimate["cost_worst_usd"] >= estimate["cost_typical_usd"] > 0


def test_annotation_export_is_blind_and_kappa_runs(world, tmp_path):
    from evaluation import annotation
    from evaluation.runner import Experiment
    from llm.client import ResponseCache as Cache

    config = dict(world["config"])
    config.update({
        "experiment": {"tasks": ["T5"], "conditions": ["U1", "FULL"], "formats": ["plain", "annotated"],
                       "item_seed": 0},
        "models": [{"name": "mock", "provider": "mock", "model": "mock-1"}],
        "results_dir": str(tmp_path / "results"), "llm_cache_path": str(tmp_path / "results" / "llm.jsonl"),
    })
    Path(config["dataset_path"]).write_text(json.dumps(world["dataset"]), encoding="utf-8")
    Experiment(config).run_models(config["models"], Cache(config["llm_cache_path"]))

    out, n = annotation.export(config, 6)
    header = (out / "annotator_A.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert n > 0 and not {"model", "condition", "format"} & set(header)
    key = json.loads((out / "sample_key.json").read_text(encoding="utf-8"))
    assert {"model", "condition", "format"} <= set(key[0])

    import csv as _csv
    for name, answer in (("annotator_A.csv", "yes"), ("annotator_B.csv", "no")):
        path = out / name
        rows = list(_csv.DictReader(path.open(encoding="utf-8")))
        for i, row in enumerate(rows):
            row["extra_claim"] = "yes" if i % 2 else answer
            row["contradicts_claims"] = "no"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = _csv.DictWriter(handle, fieldnames=annotation.FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    result = annotation.agreement(out / "annotator_A.csv", out / "annotator_B.csv")
    assert set(result) == {"extra_claim", "contradicts_claims"}
