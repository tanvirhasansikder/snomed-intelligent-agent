"""
Provider-neutral LLM access for the experiments.

* ``OpenAICompatibleClient`` calls any OpenAI-compatible ``/chat/completions``
  endpoint: Ollama (local), Groq, OpenRouter, OpenAI, Gemini's OpenAI-compatible
  endpoint, and others. Base URLs are in llm/providers.py; check them against
  each provider's current documentation.
* ``ResponseCache`` stores every response on disk keyed by the exact request,
  so re-running an experiment never pays twice and raw outputs are archived.
* ``MockLLM`` is a deterministic stand-in for tests and dry runs. Its outputs
  are synthetic and must never be reported as model results.

API keys are read from the environment variable named in the configuration
(``api_key_env``); they are sent only in the Authorization header and never
logged, cached or written to results.
"""

import hashlib
import json
import logging
import os
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """An LLM request failed after retries."""


class LLMConfigurationError(LLMError):
    """Missing key or invalid client settings."""


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = None
    completion_tokens: int = None
    latency_s: float = None
    finish_reason: str = None
    cached: bool = False
    served_by: str = None   # upstream provider reported by a router such as OpenRouter
    cost_usd: float = None  # cost reported by the provider, when it reports one

    def to_dict(self):
        return asdict(self)


def estimate_tokens(text):
    """Rough token estimate (about 4 characters per token) for cost planning only."""
    return max(1, len(text) // 4)


class OpenAICompatibleClient:

    RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

    def __init__(self, name, base_url, model, api_key_env=None, temperature=0.0, max_tokens=800,
                 timeout=120, rpm=None, json_mode=False, seed=None, max_retries=4, session=None, sleep=None,
                 extra_body=None):
        if not base_url or not model:
            raise LLMConfigurationError("base_url and model are required")
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.min_interval = (60.0 / rpm) if rpm else 0.0
        self.json_mode = json_mode
        self.seed = seed
        # Provider-specific request fields, e.g. OpenRouter provider pinning:
        # {"provider": {"order": ["deepinfra"], "allow_fallbacks": false}}
        self.extra_body = dict(extra_body or {})
        reserved = {"model", "messages"} & set(self.extra_body)
        if reserved:
            raise LLMConfigurationError(f"extra_body may not override {sorted(reserved)}")
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.sleep = sleep or time.sleep
        self._last_call = 0.0
        self.request_count = 0

        self._api_key = None
        if api_key_env:
            self._api_key = os.getenv(api_key_env)
            if not self._api_key:
                raise LLMConfigurationError(f"Environment variable {api_key_env} is not set (add it to .env)")

    def __repr__(self):
        return f"OpenAICompatibleClient(name={self.name!r}, model={self.model!r}, api_key=<hidden>)"

    def request_fingerprint(self):
        """Settings that change the output; part of the cache key (no secrets)."""
        fingerprint = {"provider": self.name, "base_url": self.base_url, "model": self.model,
                       "temperature": self.temperature, "max_tokens": self.max_tokens,
                       "json_mode": self.json_mode, "seed": self.seed}
        if self.extra_body:  # only added when used, so existing cache keys stay valid
            fingerprint["extra_body"] = self.extra_body
        return fingerprint

    def _payload(self, messages):
        payload = {"model": self.model, "messages": messages, "temperature": self.temperature,
                   "max_tokens": self.max_tokens}
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.seed is not None:
            payload["seed"] = self.seed
        payload.update(self.extra_body)
        return payload

    @staticmethod
    def _daily_quota_exhausted(response):
        """True if a 429 says a per-day quota is used up (e.g. Gemini's ...PerDay... quota IDs)."""
        try:
            text = str(response.text or "")
        except Exception:  # noqa: BLE001
            return False
        return "PerDay" in text or "per day" in text.lower()

    def _error_detail(self, response, limit=600):
        """Start of the provider's error body (helps diagnose 4xx), with any key removed."""
        try:
            text = " ".join(str(response.text or "").split())
        except Exception:  # noqa: BLE001 - diagnostics must never raise
            return ""
        if self._api_key:
            text = text.replace(self._api_key, "<hidden>")
        return f": {text[:limit]}" if text else ""

    def list_models(self):
        """
        Model identifiers this key can use (GET {base_url}/models, part of the
        OpenAI-compatible API). Raises LLMError on failure.
        """
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            response = self.session.get(f"{self.base_url}/models", headers=headers, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"{self.name}: model list failed ({type(exc).__name__})") from None
        if response.status_code != 200:
            raise LLMError(f"{self.name}: model list failed (HTTP {response.status_code}{self._error_detail(response)})")
        try:
            data = response.json().get("data", [])
            return sorted(m["id"] for m in data if isinstance(m, dict) and m.get("id"))
        except (ValueError, AttributeError, TypeError):
            raise LLMError(f"{self.name}: unexpected model list format") from None

    def _wait_for_rate_limit(self):
        if self.min_interval:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                self.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def complete(self, messages, metadata=None):
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = f"{self.base_url}/chat/completions"
        payload = self._payload(messages)
        problem = None

        for attempt in range(self.max_retries + 1):
            self._wait_for_rate_limit()
            started = time.monotonic()
            try:
                response = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
                self.request_count += 1
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                self.request_count += 1
                problem = type(exc).__name__
                self.sleep(min(60, 2 ** attempt * 2))
                continue
            except requests.exceptions.RequestException as exc:
                raise LLMError(f"{self.name}: request failed ({type(exc).__name__})") from None

            latency = time.monotonic() - started
            if response.status_code == 200:
                try:
                    data = response.json()
                    choice = data["choices"][0]
                    usage = data.get("usage") or {}
                    return LLMResponse(
                        text=choice["message"].get("content") or "",
                        model=data.get("model") or self.model,
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        latency_s=round(latency, 3),
                        finish_reason=choice.get("finish_reason"),
                        served_by=data.get("provider") if isinstance(data.get("provider"), str) else None,
                        cost_usd=usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None,
                    )
                except (ValueError, KeyError, IndexError, TypeError):
                    raise LLMError(f"{self.name}: unexpected response format") from None

            problem = f"HTTP {response.status_code}{self._error_detail(response)}"
            if response.status_code == 429 and self._daily_quota_exhausted(response):
                # Retrying cannot help until the quota resets; stop instead of wasting requests.
                raise LLMError(f"{self.name}: daily quota exhausted ({problem})")
            if response.status_code in self.RETRY_STATUS and attempt < self.max_retries:
                retry_after = (response.headers or {}).get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(60, 2 ** attempt * 2)
                except ValueError:
                    delay = min(60, 2 ** attempt * 2)
                logger.warning("%s returned %s; retrying in %.0fs", self.name, response.status_code, delay)
                self.sleep(min(delay, 120))
                continue
            raise LLMError(f"{self.name}: {problem}")

        raise LLMError(f"{self.name}: failed after {self.max_retries + 1} attempts ({problem})")


class ResponseCache:
    """Append-only JSONL cache of LLM responses keyed by the exact request."""

    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            self.entries[entry["key"]] = entry
                        except (json.JSONDecodeError, KeyError):
                            continue

    @staticmethod
    def key(fingerprint, messages):
        blob = json.dumps({"fingerprint": fingerprint, "messages": messages}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key):
        entry = self.entries.get(key)
        return None if entry is None else LLMResponse(**dict(entry["response"], cached=True))

    def put(self, key, fingerprint, response):
        entry = {"key": key, "fingerprint": fingerprint, "response": response.to_dict(),
                 "stored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.entries[key] = entry


class CachedClient:
    """Wraps a client with a ResponseCache; ``repetition`` separates repeated runs."""

    def __init__(self, client, cache):
        self.client = client
        self.cache = cache
        self.name = client.name
        self.model = client.model

    def complete(self, messages, metadata=None, repetition=0):
        fingerprint = dict(self.client.request_fingerprint(), repetition=repetition)
        key = ResponseCache.key(fingerprint, messages)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        response = self.client.complete(messages, metadata=metadata)
        self.cache.put(key, fingerprint, response)
        return response


class MockLLM:
    """
    Deterministic SYNTHETIC model for tests and pipeline dry runs.

    Behaviour (seeded per item):
    * on sufficient context, returns the evidence-only reference answer;
    * on insufficient context, with probability ``fill_in_rate`` answers from
      the gold answer while tagging claims as "evidence" (misattributed fill-in),
      with probability ``hallucination_rate`` adds a false claim, otherwise
      abstains;
    * with plain context, claims "no children" when children were not
      retrieved, at rate ``false_absence_rate``; annotated context halves it.
    """

    def __init__(self, name="mock", model="mock-1", fill_in_rate=0.4, hallucination_rate=0.2,
                 false_absence_rate=0.5, seed=0, **_ignored):
        self.name = name
        self.model = model
        self.fill_in_rate = fill_in_rate
        self.hallucination_rate = hallucination_rate
        self.false_absence_rate = false_absence_rate
        self.seed = seed
        self.request_count = 0
        self.temperature = 0.0
        self.max_tokens = 0
        self.json_mode = True

    def request_fingerprint(self):
        return {"provider": "mock", "model": self.model, "fill_in_rate": self.fill_in_rate,
                "hallucination_rate": self.hallucination_rate, "false_absence_rate": self.false_absence_rate,
                "seed": self.seed}

    def complete(self, messages, metadata=None):
        self.request_count += 1
        meta = metadata or {}
        rng = random.Random(f"{self.seed}:{meta.get('item_id')}:{meta.get('condition')}:{meta.get('format')}")
        answer = json.loads(json.dumps(meta.get("reference_answer") or {"answer": None, "claims": []}))
        task = meta.get("task")
        x = meta.get("concept_id")

        if not meta.get("sufficient"):
            roll = rng.random()
            if roll < self.fill_in_rate and meta.get("gold_answer") is not None:
                answer = json.loads(json.dumps(meta["gold_answer"]))
                for claim in answer.get("claims", []):
                    claim["source"] = "evidence"
            elif roll < self.fill_in_rate + self.hallucination_rate:
                answer["insufficient_evidence"] = False
                answer.setdefault("claims", []).append(
                    {"subject": x, "relation": "direct_parent", "object": "1000004", "source": "evidence"})
                if task == "T1":
                    answer["answer"] = list(answer.get("answer") or []) + ["1000004"]
                elif task == "T3":
                    answer["answer"] = "yes"
            else:
                answer["insufficient_evidence"] = True
                if task == "T3":
                    answer["answer"] = "unknown"

        if task in ("T2", "T5") and not meta.get("children_retrieved"):
            rate = self.false_absence_rate * (0.5 if meta.get("format") == "annotated" else 1.0)
            if rng.random() < rate:
                answer.setdefault("claims", []).append(
                    {"subject": x, "relation": "no_children", "object": None, "source": "evidence"})
                if task == "T2":
                    answer["answer"] = []
                    answer["insufficient_evidence"] = False

        text = json.dumps(answer)
        prompt_text = " ".join(m["content"] for m in messages)
        return LLMResponse(text=text, model=self.model, prompt_tokens=estimate_tokens(prompt_text),
                           completion_tokens=estimate_tokens(text), latency_s=0.0, finish_reason="stop")
