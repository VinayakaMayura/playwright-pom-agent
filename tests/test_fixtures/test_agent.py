from playwright_pom_agent.agent import LLMProvider, POMAgent
from playwright_pom_agent.inspector import inspect_page
from playwright_pom_agent.models import VerificationResult

from conftest import fixture_url


def _make_agent(provider: LLMProvider, max_locator_retries: int = 3) -> POMAgent:
    agent = object.__new__(POMAgent)
    agent.max_locator_retries = max_locator_retries
    agent._llm = provider
    agent._warned = True  # skip the stderr warning noise in tests
    return agent


class ScriptedProvider(LLMProvider):
    """Returns one scripted response per call, in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def propose(self, system, user, schema):
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


def test_refinement_loop_recovers_from_ambiguous_locator(page):
    page.goto(fixture_url("login.html"))
    ctx = inspect_page(page)

    provider = ScriptedProvider(
        [
            {"elements": [{"name": "any_input", "locator": {"strategy": "css", "value": "input"}}]},
            {"elements": [{"name": "any_input", "locator": {"strategy": "test_id", "value": "email-input"}}]},
        ]
    )
    agent = _make_agent(provider, max_locator_retries=3)

    resolved = agent.resolve_elements(page, ctx)

    assert provider.calls == 2
    assert len(resolved) == 1
    assert resolved[0].status == VerificationResult.UNIQUE
    assert resolved[0].locator.strategy == "test_id"


def test_refinement_loop_stops_after_max_retries_and_flags_element(page):
    page.goto(fixture_url("login.html"))
    ctx = inspect_page(page)

    # Always proposes the same ambiguous locator - never resolves.
    provider = ScriptedProvider(
        [{"elements": [{"name": "any_input", "locator": {"strategy": "css", "value": "input"}}]}]
    )
    agent = _make_agent(provider, max_locator_retries=2)

    resolved = agent.resolve_elements(page, ctx)

    assert provider.calls == 2  # bounded, not infinite
    assert len(resolved) == 1
    assert resolved[0].status == VerificationResult.AMBIGUOUS
    assert resolved[0].attempts == 2


def test_resolved_elements_are_not_reproposed(page):
    page.goto(fixture_url("login.html"))
    ctx = inspect_page(page)

    provider = ScriptedProvider(
        [
            {
                "elements": [
                    {"name": "email_input", "locator": {"strategy": "test_id", "value": "email-input"}},
                    {"name": "any_input", "locator": {"strategy": "css", "value": "input"}},
                ]
            },
            {"elements": [{"name": "any_input", "locator": {"strategy": "test_id", "value": "submit-form"}}]},
        ]
    )
    agent = _make_agent(provider, max_locator_retries=3)

    resolved = agent.resolve_elements(page, ctx)
    names = {r.name for r in resolved}
    assert names == {"email_input", "any_input"}
    assert all(r.status == VerificationResult.UNIQUE for r in resolved)
