"""System runner (protocol v2.0): R0/B0/B1/S1/S2 through the shared pipeline."""

import json

import pytest

from evaluation import runner
from evaluation.bioportal_cache import CallStore
from evaluation.collect import collect, save_dataset
from evaluation.synthetic import SyntheticOntology
from evaluation.system_runner import SystemExperiment
from llm.client import ResponseCache

MOCK = {"name": "mock", "provider": "mock", "model": "mock-1",
        "mock": {"fill_in_rate": 0.4, "hallucination_rate": 0.2, "false_absence_rate": 0.5, "seed": 0}}


def make_config(tmp_path, budget=60, systems=("R0", "B0", "B1", "S1", "S2")):
    return {
        "name": "sys-test", "synthetic": True,
        "cache_path": str(tmp_path / "calls.jsonl"), "dataset_path": str(tmp_path / "ds.json"),
        "data": {"per_hierarchy": 3, "seed": 5, "min_depth": 2, "max_depth": 6},
        "experiment": {"systems": list(systems), "tasks": ["T1", "T2", "T3", "T4", "T5", "T6"],
                       "request_budget": budget, "item_seed": 0},
        "models": [MOCK],
        "results_dir": str(tmp_path / "results"), "llm_cache_path": str(tmp_path / "results" / "llm.jsonl"),
    }


@pytest.fixture
def data(tmp_path):
    live = SyntheticOntology(seed=0)
    config = make_config(tmp_path)
    config["data"]["hierarchies"] = live.top_level_names()
    save_dataset(config, collect(config, live, CallStore(config["cache_path"])))
    return config


def run(config):
    experiment = SystemExperiment(config)
    experiment.run_reference()
    made = experiment.run_systems(config["models"], ResponseCache(config["llm_cache_path"]))
    rows = [json.loads(line) for line in experiment.responses_path.read_text(encoding="utf-8").splitlines()]
    return experiment, rows, made


def test_every_system_answers_every_item(data):
    experiment, rows, _ = run(data)
    n = len(experiment.items)
    counts = {s: sum(1 for r in rows if r["system"] == s) for s in ("R0-B1", "R0-S1", "B0", "B1", "S1", "S2")}
    assert all(c == n for c in counts.values()), counts
    assert all(r["protocol_version"] == "2.0" and r["error"] is None for r in rows)
    assert {r["retrieval"] for r in rows if r["system"] in ("S1", "S2", "R0-S1")} == {"S1"}


def test_s2_reuses_s1_call_and_pairs_exactly(data):
    _, rows, _ = run(data)
    s1 = {r["item_id"]: r for r in rows if r["system"] == "S1"}
    for r in (r for r in rows if r["system"] == "S2"):
        assert r["raw_text"] == s1[r["item_id"]]["raw_text"]
        assert r["n_new_calls"] == (1 if r["regenerated"] else 0)


def test_reference_on_s1_evidence_is_perfect_when_sufficient(data):
    _, rows, _ = run(data)
    full = [r for r in rows if r["system"] == "R0-S1" and r["sufficient"] and r["task"] != "T5"]
    assert full and all(r["score"] == 1.0 for r in full)


def test_resume_makes_no_new_calls(data):
    run(data)
    _, rows_again, made = run(data)
    assert made == 0
    assert len(rows_again) == len({(r["system"], r["item_id"], r["repetition"]) for r in rows_again})


def test_small_budget_exercises_verification(tmp_path):
    live = SyntheticOntology(seed=0)
    config = make_config(tmp_path, budget=3)
    config["data"]["hierarchies"] = live.top_level_names()
    save_dataset(config, collect(config, live, CallStore(config["cache_path"])))
    _, rows, _ = run(config)
    s1 = [r for r in rows if r["system"] == "S1"]
    s2 = [r for r in rows if r["system"] == "S2"]
    assert any(r["budget_stopped"] for r in s2)
    assert sum(r["n_claims"] - r["n_supported_by_subgraph"] for r in s1) > 0
    assert sum(r["n_claims"] - r["n_supported_by_subgraph"] for r in s2) == 0       # faithful by construction
    assert sum(r.get("n_false_in_reference", 0) for r in s2) == 0
    assert any(r["regenerated"] for r in s2)


def test_runner_dispatches_to_systems(data, tmp_path, capsys):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    runner._main(["--config", str(path)])
    out = capsys.readouterr().out
    assert "systems: ['R0', 'B0', 'B1', 'S1', 'S2']" in out
    assert (tmp_path / "results" / "system_responses.jsonl").exists()
    runner._main(["--config", str(path), "--dry-run"])
    dry = capsys.readouterr().out
    assert "Items:" in dry and "new calls" not in dry      # everything already done: nothing left to estimate


def test_unknown_system_is_rejected(data):
    data["experiment"]["systems"] = ["R0", "S9"]
    with pytest.raises(ValueError):
        SystemExperiment(data)


def test_run_stops_when_daily_quota_is_exhausted(data, monkeypatch):
    from evaluation import system_runner
    from llm.client import LLMError

    class QuotaGone:
        name, model = "fake", "fake-1"

        def request_fingerprint(self):
            return {"provider": "fake"}

        def complete(self, messages, metadata=None):
            raise LLMError("fake: daily quota exhausted (HTTP 429: GenerateRequestsPerDay)")

    monkeypatch.setattr(system_runner, "build_client", lambda spec: QuotaGone())
    experiment = SystemExperiment(data)
    made = experiment.run_systems(data["models"], ResponseCache(data["llm_cache_path"]))
    rows = [json.loads(line) for line in experiment.responses_path.read_text(encoding="utf-8").splitlines()]
    assert made == 0 and len(rows) == 1 and experiment.stopped_reason == "daily quota exhausted"


def test_pilot_and_main_configs_use_v2_systems():
    from evaluation.collect import load_config

    for name in ("pilot", "main"):
        config = load_config(f"configs/{name}.json")
        assert config["experiment"]["systems"] == ["R0", "B0", "B1", "S1", "S2"]
        assert config["experiment"]["tasks"] == ["T1", "T2", "T3", "T4", "T5", "T6"]
        assert config["models"][0]["model"] == "gemini-3.5-flash-lite"
        assert config["models"][0]["extra_body"] == {"reasoning_effort": "low"}
    assert load_config("configs/pilot.json")["data"]["per_hierarchy"] == 2
    assert load_config("configs/main.json")["data"]["exclude_samples_from"] == ["data/datasets/pilot.json"]
