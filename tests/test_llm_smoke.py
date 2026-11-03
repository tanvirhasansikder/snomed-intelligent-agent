"""Offline tests for the live smoke test (no network)."""

import json

import pytest

from llm.client import LLMError, OpenAICompatibleClient
from llm.smoke import CHILD, P1, P2, TOP, X, run_smoke
from tests.fakes import FakeResponse


def _reply(answer, claims):
    body = {"answer": answer, "claims": claims, "insufficient_evidence": False}
    return FakeResponse(200, {"model": "m-1", "choices": [{"message": {"content": json.dumps(body)},
                                                           "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


class Session:
    def __init__(self, posts=(), gets=()):
        self.posts, self.gets, self.calls = list(posts), list(gets), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        return self.posts.pop(0)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return self.gets.pop(0)


def ev(s, r, o=None):
    return {"subject": s, "relation": r, "object": o, "source": "evidence"}


def test_run_smoke_scores_correct_answers():
    session = Session(posts=[
        _reply([P1, P2], [ev(X, "direct_parent", P1), ev(X, "direct_parent", P2)]),
        _reply([CHILD], []),
        _reply("yes", [ev(X, "ancestor", TOP)]),
        _reply([X, P1, TOP], [ev(X, "direct_parent", P1), ev(P1, "direct_parent", TOP)]),
        FakeResponse(200, {"model": "m-1", "choices": [{"message": {"content": "no json"},
                                                        "finish_reason": "stop"}]}),
    ])
    client = OpenAICompatibleClient("p", "https://example.invalid/v1", "m", session=session, sleep=lambda s: None)

    rows = run_smoke(client)

    by_task = {r["task"]: r for r in rows}
    assert [by_task[t]["score"] for t in ("T1", "T2", "T3", "T4")] == [1.0, 1.0, 1.0, 1.0]
    assert by_task["T1"]["n_unsupported"] == 0
    assert by_task["T5"]["parse_ok"] is False
    assert "Synthetic inflammation of organ A" in session.calls[0]["messages"][1]["content"]


def test_run_smoke_records_request_failures():
    session = Session(posts=[FakeResponse(401, text="bad key")] * 5)
    client = OpenAICompatibleClient("p", "https://example.invalid/v1", "m", session=session, sleep=lambda s: None)
    rows = run_smoke(client)
    assert all(not r["ok"] and "HTTP 401" in r["error"] for r in rows)


def test_list_models(monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "secret")
    session = Session(gets=[FakeResponse(200, {"data": [{"id": "models/b"}, {"id": "models/a"}]}),
                            FakeResponse(403, text="no")])
    client = OpenAICompatibleClient("p", "https://example.invalid/v1", "m", api_key_env="TEST_LLM_KEY",
                                    session=session, sleep=lambda s: None)
    assert client.list_models() == ["models/a", "models/b"]
    assert session.calls[0] == "https://example.invalid/v1/models"
    with pytest.raises(LLMError):
        client.list_models()


def test_error_message_includes_provider_detail_without_key(monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "sk-secret-999")
    body = '{"error": {"code": 404, "message": "models/x is not found for key sk-secret-999"}}'
    session = Session(posts=[FakeResponse(404, text=body)])
    client = OpenAICompatibleClient("p", "https://example.invalid/v1", "m", api_key_env="TEST_LLM_KEY",
                                    session=session, sleep=lambda s: None)
    with pytest.raises(LLMError) as info:
        client.complete([{"role": "user", "content": "hi"}])
    message = str(info.value)
    assert "HTTP 404" in message and "is not found" in message
    assert "sk-secret-999" not in message and "<hidden>" in message


def test_daily_quota_429_is_not_retried():
    body = '{"error": {"code": 429, "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}'
    session = Session(posts=[FakeResponse(429, text=body), OK_LATER])
    client = OpenAICompatibleClient("p", "https://example.invalid/v1", "m", session=session, sleep=lambda s: None)
    with pytest.raises(LLMError, match="daily quota exhausted"):
        client.complete([{"role": "user", "content": "hi"}])
    assert len(session.calls) == 1


def test_max_retries_is_configurable():
    from llm.providers import build_client

    spec = {"name": "x", "provider": "ollama", "model": "m", "max_retries": 1}
    client = build_client(spec)
    assert client.max_retries == 1
    session = Session(posts=[FakeResponse(503, text="busy")] * 2)
    client.session, client.sleep = session, (lambda s: None)
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "hi"}])
    assert len(session.calls) == 2


OK_LATER = _reply([], [])
