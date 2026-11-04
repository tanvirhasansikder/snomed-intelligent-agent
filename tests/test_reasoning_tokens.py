"""
Protocol Amendment 3: hidden reasoning tokens and truncation (offline, no network).

A pilot answer stopped with finish_reason "length" after 192 reported completion
tokens under an 800-token limit, so the limit was raised to 2,000 and the
hidden reasoning tokens are now recorded when the provider reports them.
"""

import json
from pathlib import Path

from evaluation.system_report import summarise, tradeoffs
from llm.client import LLMResponse, OpenAICompatibleClient, ResponseCache
from tests.fakes import FakeResponse

ROOT = Path(__file__).resolve().parent.parent


class Session:
    def __init__(self, response):
        self.response = response

    def post(self, url, json=None, headers=None, timeout=None):
        return self.response


def _client(usage):
    body = {"model": "m-1", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": usage}
    return OpenAICompatibleClient("p", "https://example.invalid/v1", "m", session=Session(FakeResponse(200, body)),
                                  sleep=lambda s: None)


def test_reasoning_tokens_are_recorded_when_reported():
    usage = {"prompt_tokens": 380, "completion_tokens": 192, "completion_tokens_details": {"reasoning_tokens": 600}}
    response = _client(usage).complete([{"role": "user", "content": "hi"}])
    assert response.completion_tokens == 192 and response.reasoning_tokens == 600


def test_reasoning_tokens_are_none_when_absent_or_malformed():
    for usage in ({"prompt_tokens": 1, "completion_tokens": 2},
                  {"completion_tokens_details": None},
                  {"completion_tokens_details": {"reasoning_tokens": "many"}}):
        assert _client(usage).complete([{"role": "user", "content": "hi"}]).reasoning_tokens is None


def test_old_cache_entries_without_the_field_still_load(tmp_path):
    path = tmp_path / "cache.jsonl"
    old = {"text": "{}", "model": "m", "prompt_tokens": 1, "completion_tokens": 2, "latency_s": 0.1,
           "finish_reason": "stop", "cached": False, "served_by": None, "cost_usd": None}
    path.write_text(json.dumps({"key": "k", "fingerprint": {}, "response": old}) + "\n", encoding="utf-8")
    response = ResponseCache(path).get("k")
    assert isinstance(response, LLMResponse) and response.cached and response.reasoning_tokens is None


def test_pilot_and_main_configs_use_one_output_limit():
    for name in ("pilot.json", "main.json"):
        config = json.loads((ROOT / "configs" / name).read_text(encoding="utf-8"))
        assert len({m["max_tokens"] for m in config["models"]}) == 1, name


def _row(item, finish, reasoning):
    return {"system": "B0", "model_name": "m", "item_id": item, "repetition": 0, "task": "T5", "error": None,
            "n_claims": 0, "n_supported_by_subgraph": 0, "n_false_in_reference": 0, "n_unknown_identifier": 0,
            "score": 0.0, "finish_reason": finish, "reasoning_tokens": reasoning}


def test_report_shows_truncation_rate_and_reasoning_tokens():
    rows = [_row("i1", "length", 600), _row("i2", "stop", 200), _row("i3", "stop", None), _row("i4", "stop", 100)]
    overall = next(s for s in summarise(rows) if s["task"] == "ALL")
    assert overall["truncated_rate"] == 0.25
    assert overall["mean_reasoning_tokens"] == 300
    trade = tradeoffs(summarise(rows))[0]
    assert "truncated_rate" in trade and "mean_reasoning_tokens" in trade


# ----------------------------------------------------------------------
# A settings change makes the runner collect the answer again (Amendment 3)
# ----------------------------------------------------------------------

from evaluation.system_report import load_system_rows  # noqa: E402
from evaluation.system_runner import SystemExperiment, model_settings  # noqa: E402
from tests.test_system_runner import data, run  # noqa: E402,F401  (pytest fixture + helper)


def test_rows_record_model_settings(data):  # noqa: F811
    _, rows, _ = run(data)
    llm_rows = [r for r in rows if r["model_name"] == "mock"]
    assert llm_rows and all(r["model_settings"] == model_settings(data["models"][0]) for r in llm_rows)


def test_changed_max_tokens_collects_again_and_report_keeps_the_new_rows(data):  # noqa: F811
    run(data)
    data["models"][0]["max_tokens"] = 2000
    experiment = SystemExperiment(data)
    assert len(experiment.plan_systems(data["models"])) == 4 * len(experiment.items)
    run(data)   # the mock's cache key ignores max_tokens, so these come from the cache, but the rows are new
    assert SystemExperiment(data).plan_systems(data["models"]) == []
    latest = [r for r in load_system_rows(experiment.responses_path) if r["model_name"] == "mock"]
    assert latest and all(r["model_settings"]["max_tokens"] == 2000 for r in latest)


def test_rows_without_recorded_settings_are_collected_again(data):  # noqa: F811
    experiment, _, _ = run(data)
    path = experiment.responses_path
    old = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for r in old:
        r.pop("model_settings", None)
    path.write_text("".join(json.dumps(r) + "\n" for r in old), encoding="utf-8")
    fresh = SystemExperiment(data)
    assert len(fresh.plan_systems(data["models"])) == 4 * len(fresh.items)
    assert fresh.run_reference() == 0   # R0 needs no model, so its rows still count


def test_report_never_keeps_an_old_settings_row_whose_retry_failed(tmp_path):
    path = tmp_path / "rows.jsonl"
    new = {"max_tokens": 2000}
    rows = [
        {"system": "B0", "model_name": "g", "item_id": "a", "error": None, "answer": "old"},              # 800 tokens, no settings
        {"system": "B0", "model_name": "g", "item_id": "b", "error": None, "answer": "old"},
        {"system": "B0", "model_name": "g", "item_id": "a", "error": "HTTP 503", "model_settings": new},  # retry failed
        {"system": "B0", "model_name": "g", "item_id": "b", "error": None, "answer": "new", "model_settings": new},
        {"system": "R0-B1", "model_name": "R0", "item_id": "a", "error": None, "answer": "ref"},           # no model: kept
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    kept = {(r["system"], r["item_id"]): r for r in load_system_rows(path)}
    assert kept[("B0", "a")]["error"] == "HTTP 503"
    assert kept[("B0", "b")]["answer"] == "new"
    assert kept[("R0-B1", "a")]["answer"] == "ref"


# ----------------------------------------------------------------------
# Amendment 5: a cut-off reply is a parse failure, not an empty answer
# ----------------------------------------------------------------------

from llm.reasoner import parse_answer  # noqa: E402


def test_cut_off_reply_is_a_parse_failure_not_its_first_claim():
    cut = ('{"answer": ["1001000", "1002000", "1003000"], "claims": [{"subject": "1001000", "relation": "child_of", '
           '"object": "1000000", "source": "evidence", "cites": ["E1"]}, {"subject": "1002000", "relat')
    parsed = parse_answer(cut, "T2")
    assert parsed["parse_error"] is not None and "answer" in parsed["parse_error"]
    assert parsed["answer"] is None and parsed["claims"] == []


def test_complete_reply_still_parses_inside_text_or_fences():
    reply = '{"answer": ["1001000"], "claims": [], "insufficient_evidence": false}'
    for text in (reply, "Here it is:\n```json\n" + reply + "\n```", "note {\"x\": 1} then " + reply):
        parsed = parse_answer(text, "T2")
        assert parsed["parse_error"] is None and parsed["answer"] == ["1001000"], text


def test_configs_allow_8192_output_tokens():
    for name in ("pilot.json", "main.json"):
        config = json.loads((ROOT / "configs" / name).read_text(encoding="utf-8"))
        assert all(m["max_tokens"] == 8192 for m in config["models"]), name
