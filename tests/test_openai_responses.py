import json
from urllib.error import HTTPError, URLError

import pytest

from app.ai.errors import (
    PolicyViolation,
    RateLimited,
    TerminalProviderError,
    TransientProviderError,
    UsageLimitExceeded,
)
from app.ai.openai_responses import (
    MAX_FACT_CHARS,
    MAX_FACTS,
    OpenAIResponsesCopyProvider,
    RetryPolicy,
)
from app.ai.product_copy import ProductCopyRequest
from app.ai.usage import UsageLedger, UsageLimit

GOOD_COPY = {
    "title": "Box",
    "description": "Borosilicate glass",
    "claims": [],
    "source_facts": ["Borosilicate glass"],
}


class Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def payload(copy=None, usage=None):
    return {"output_text": json.dumps(copy or GOOD_COPY), "usage": usage or {}}


def provider(**kwargs):
    kwargs.setdefault("sleep", lambda _: None)
    return OpenAIResponsesCopyProvider("key", **kwargs)


def request():
    return ProductCopyRequest("Box", ("Borosilicate glass",), "GB")


def install(monkeypatch, handler):
    calls = []

    def fake_urlopen(http_request, timeout=None):
        calls.append(http_request)
        return handler(len(calls), http_request)

    monkeypatch.setattr("app.ai.openai_responses.urlopen", fake_urlopen)
    return calls


def raising(exc_factory):
    def handler(n, _):
        raise exc_factory()

    return handler


def http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return HTTPError("https://api.openai.com/v1/responses", code, "err", headers, None)


# --- basics -----------------------------------------------------------------


def test_requires_key():
    with pytest.raises(ValueError):
        OpenAIResponsesCopyProvider("").generate(request())


def test_schema_is_included_in_the_request(monkeypatch):
    calls = install(monkeypatch, lambda n, r: Response(payload()))
    assert provider().generate(request()).title == "Box"
    body = json.loads(calls[0].data)
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True


def test_output_blocks_are_read_when_output_text_is_absent(monkeypatch):
    blocks = {
        "output": [{"content": [{"text": json.dumps(GOOD_COPY)}]}],
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }
    install(monkeypatch, lambda n, r: Response(blocks))
    assert provider().generate(request()).title == "Box"


# --- retries ----------------------------------------------------------------


def test_transient_failures_are_retried_then_succeed(monkeypatch):
    def handler(n, _):
        if n < 3:
            raise http_error(503)
        return Response(payload())

    install(monkeypatch, handler)
    assert provider().generate(request()).title == "Box"


def test_retries_are_bounded(monkeypatch):
    calls = install(monkeypatch, raising(lambda: http_error(500)))
    with pytest.raises(TransientProviderError):
        provider(retries=RetryPolicy(max_attempts=3)).generate(request())
    assert len(calls) == 3


def test_network_errors_are_transient(monkeypatch):
    install(monkeypatch, raising(lambda: URLError("no route")))
    with pytest.raises(TransientProviderError):
        provider(retries=RetryPolicy(max_attempts=1)).generate(request())


def test_timeouts_are_transient(monkeypatch):
    install(monkeypatch, raising(TimeoutError))
    with pytest.raises(TransientProviderError):
        provider(retries=RetryPolicy(max_attempts=1)).generate(request())


def test_client_errors_are_not_retried(monkeypatch):
    """A 400 is a request to fix, not one to resend."""
    calls = install(monkeypatch, raising(lambda: http_error(400)))
    with pytest.raises(TerminalProviderError):
        provider().generate(request())
    assert len(calls) == 1


def test_auth_failure_is_not_retried(monkeypatch):
    calls = install(monkeypatch, raising(lambda: http_error(401)))
    with pytest.raises(TerminalProviderError):
        provider().generate(request())
    assert len(calls) == 1


def test_rate_limit_honours_retry_after(monkeypatch):
    slept = []

    def handler(n, _):
        if n == 1:
            raise http_error(429, retry_after="2")
        return Response(payload())

    install(monkeypatch, handler)
    provider(sleep=slept.append).generate(request())
    assert slept == [2.0]


def test_retry_after_is_capped_by_the_policy(monkeypatch):
    slept = []
    install(monkeypatch, raising(lambda: http_error(429, retry_after="9999")))
    with pytest.raises(RateLimited):
        provider(sleep=slept.append, retries=RetryPolicy(max_delay=8.0)).generate(request())
    assert slept and all(delay <= 8.0 for delay in slept)


def test_backoff_is_jittered():
    policy = RetryPolicy(base_delay=1.0, max_delay=30.0)
    delays = {policy.backoff(3) for _ in range(50)}
    assert len(delays) > 1  # not a fixed schedule every worker replays together
    assert all(0 <= d <= 4.0 for d in delays)


# --- spend ------------------------------------------------------------------


def test_call_budget_stops_requests(monkeypatch):
    calls = install(monkeypatch, lambda n, r: Response(payload()))
    ledger = UsageLedger(UsageLimit(max_calls=2, max_total_tokens=10_000))
    client = provider(usage=ledger)
    client.generate(request())
    client.generate(request())
    with pytest.raises(UsageLimitExceeded):
        client.generate(request())
    assert len(calls) == 2


def test_token_budget_stops_requests(monkeypatch):
    spend = {"input_tokens": 400, "output_tokens": 200}
    install(monkeypatch, lambda n, r: Response(payload(usage=spend)))
    ledger = UsageLedger(UsageLimit(max_calls=99, max_total_tokens=500))
    client = provider(usage=ledger)
    client.generate(request())
    assert ledger.total_tokens == 600
    with pytest.raises(UsageLimitExceeded):
        client.generate(request())


def test_retries_consume_budget(monkeypatch):
    """A retry is a real request; it must not be free."""
    install(monkeypatch, raising(lambda: http_error(500)))
    ledger = UsageLedger(UsageLimit(max_calls=10, max_total_tokens=10_000))
    with pytest.raises(TransientProviderError):
        provider(usage=ledger, retries=RetryPolicy(max_attempts=3)).generate(request())
    assert ledger.calls == 3


# --- untrusted input --------------------------------------------------------


def test_facts_are_sent_as_structured_data(monkeypatch):
    calls = install(monkeypatch, lambda n, r: Response(payload()))
    provider().generate(ProductCopyRequest("Box", ("Glass", "500ml"), "GB"))
    body = json.loads(calls[0].data)
    assert json.loads(body["input"])["verified_facts"] == ["Glass", "500ml"]
    assert "untrusted supplier data" in body["instructions"]


def test_control_characters_are_stripped_from_facts(monkeypatch):
    calls = install(monkeypatch, lambda n, r: Response(payload()))
    provider().generate(ProductCopyRequest("Box", ("Gla\x00ss\x1b[31m",), "GB"))
    facts = json.loads(json.loads(calls[0].data)["input"])["verified_facts"]
    assert "\x00" not in facts[0] and "\x1b" not in facts[0]


def test_fact_count_and_length_are_capped(monkeypatch):
    calls = install(monkeypatch, lambda n, r: Response(payload()))
    provider().generate(ProductCopyRequest("Box", tuple("x" * 900 for _ in range(80)), "GB"))
    facts = json.loads(json.loads(calls[0].data)["input"])["verified_facts"]
    assert len(facts) == MAX_FACTS
    assert all(len(f) <= MAX_FACT_CHARS for f in facts)


def test_empty_facts_are_refused_before_any_request(monkeypatch):
    calls = install(monkeypatch, lambda n, r: Response(payload()))
    with pytest.raises(ValueError):
        provider().generate(ProductCopyRequest("Box", ("   ",), "GB"))
    assert calls == []


# --- output safety ----------------------------------------------------------


def test_policy_violating_copy_never_escapes_the_provider(monkeypatch):
    banned = dict(GOOD_COPY, description="Cures acne and guaranteed results")
    install(monkeypatch, lambda n, r: Response(payload(banned)))
    with pytest.raises(PolicyViolation) as exc:
        provider().generate(request())
    assert exc.value.reasons


def test_unparseable_output_is_terminal(monkeypatch):
    install(monkeypatch, lambda n, r: Response({"output_text": "not json"}))
    with pytest.raises(TerminalProviderError):
        provider().generate(request())


def test_missing_output_is_terminal(monkeypatch):
    install(monkeypatch, lambda n, r: Response({"usage": {}}))
    with pytest.raises(TerminalProviderError):
        provider().generate(request())


def test_errors_do_not_leak_the_api_key(monkeypatch):
    install(monkeypatch, raising(lambda: http_error(401)))
    with pytest.raises(TerminalProviderError) as exc:
        OpenAIResponsesCopyProvider("sk-super-secret", sleep=lambda _: None).generate(request())
    assert "sk-super-secret" not in str(exc.value)


# --- transport --------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url", ["http://api.example.test/v1", "https://user:pw@api.openai.com/v1"]
)
def test_unsafe_base_urls_are_refused(base_url):
    with pytest.raises(ValueError):
        OpenAIResponsesCopyProvider("key", base_url=base_url)


def test_local_test_server_is_allowed():
    assert OpenAIResponsesCopyProvider("key", base_url="http://localhost:9/v1")


def test_bearer_token_is_sent(monkeypatch):
    calls = install(monkeypatch, lambda n, r: Response(payload()))
    provider().generate(request())
    assert calls[0].headers["Authorization"] == "Bearer key"


def test_token_cap_can_overshoot_by_one_call(monkeypatch):
    """A request's cost is unknown until its response arrives, so the token cap
    stops the call *after* the breach, not the one that causes it. The call cap
    is the exact one."""
    spend = {"input_tokens": 120, "output_tokens": 80}
    install(monkeypatch, lambda n, r: Response(payload(usage=spend)))
    ledger = UsageLedger(UsageLimit(max_calls=99, max_total_tokens=300))
    client = provider(usage=ledger)
    client.generate(request())
    client.generate(request())
    assert ledger.total_tokens == 400 > 300
    with pytest.raises(UsageLimitExceeded):
        client.generate(request())


def test_missing_key_is_reported_before_request_shape(monkeypatch):
    """The error an operator hits on a misconfigured deployment must name the key."""
    calls = install(monkeypatch, lambda n, r: Response(payload()))
    with pytest.raises(ValueError, match="api_key"):
        OpenAIResponsesCopyProvider("").generate(ProductCopyRequest("Box", (), "GB"))
    assert calls == []


def test_a_shared_ledger_caps_across_provider_instances(monkeypatch):
    """Per-instance ledgers would reset the cap; a shared one is the way to a
    process-wide budget."""
    install(monkeypatch, lambda n, r: Response(payload()))
    shared = UsageLedger(UsageLimit(max_calls=2, max_total_tokens=10_000))
    provider(usage=shared).generate(request())
    provider(usage=shared).generate(request())
    with pytest.raises(UsageLimitExceeded):
        provider(usage=shared).generate(request())
