"""Reference tests against a real, live website: https://demo.realworld.show/
(the "Conduit" RealWorld demo app - an Angular SPA).

Unlike the local fixtures elsewhere in this suite, this app has no
`data-testid` attributes and no `<label>` tags anywhere - every accessible
name comes from `placeholder`, so these tests exercise the role/placeholder
fallback tiers for real, on markup this project didn't author. Use this file
as a template for pointing playwright-pom-agent at your own app: swap
BASE_URL/TEST_EMAIL/TEST_PASSWORD (or set the REALWORLD_* env vars below).

These tests require internet access and are marked ``live`` so they can be
excluded in offline environments: ``pytest tests/ -m "not live"``.
"""
from __future__ import annotations

import ast
import os
import re
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from playwright_pom_agent.agent import LLMProvider, POMAgent
from playwright_pom_agent.cli import app
from playwright_pom_agent.inspector import inspect_page
from playwright_pom_agent.locator_spec import LocatorSpec
from playwright_pom_agent.models import VerificationResult
from playwright_pom_agent.verifier import verify

pytestmark = pytest.mark.live

PAGES_DIR = Path(__file__).parent / "pages"

BASE_URL = os.environ.get("REALWORLD_BASE_URL", "https://demo.realworld.show")
LOGIN_URL = f"{BASE_URL}/login"
REGISTER_URL = f"{BASE_URL}/register"
SETTINGS_URL = f"{BASE_URL}/settings"
EDITOR_URL = f"{BASE_URL}/editor"
TEST_EMAIL = os.environ.get("REALWORLD_TEST_EMAIL", "test@test.com")
TEST_PASSWORD = os.environ.get("REALWORLD_TEST_PASSWORD", "asdf1234")


def _role_textbox(name: str) -> LocatorSpec:
    return LocatorSpec(strategy="role", value="textbox", role_name=name)


def _role_button(name: str) -> LocatorSpec:
    return LocatorSpec(strategy="role", value="button", role_name=name)


def _login(page) -> None:
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.get_by_role("textbox", name="Email").fill(TEST_EMAIL)
    page.get_by_role("textbox", name="Password").fill(TEST_PASSWORD)
    with page.expect_response(lambda r: r.url.endswith("/login") and r.request.method == "POST", timeout=15000):
        page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{BASE_URL}/", timeout=15000)
    page.wait_for_load_state("networkidle")


def test_login_page_role_locators_resolve_uniquely(page):
    page.goto(LOGIN_URL, wait_until="networkidle")
    assert verify(page, _role_textbox("Email")).result == VerificationResult.UNIQUE
    assert verify(page, _role_textbox("Password")).result == VerificationResult.UNIQUE
    assert verify(page, _role_button("Sign in")).result == VerificationResult.UNIQUE


def test_login_page_placeholder_fallback_also_resolves_uniquely(page):
    # No <label> tags exist on this app; the accessible name is derived from
    # placeholder text, so both the role tier and the placeholder tier work.
    # Role (tier 2) is still preferred over placeholder (tier 4) when both apply.
    page.goto(LOGIN_URL, wait_until="networkidle")
    assert verify(page, LocatorSpec(strategy="placeholder", value="Email")).result == VerificationResult.UNIQUE
    assert verify(page, LocatorSpec(strategy="placeholder", value="Password")).result == VerificationResult.UNIQUE


def test_register_page_locators_resolve_uniquely(page):
    page.goto(REGISTER_URL, wait_until="networkidle")
    for name in ("Username", "Email", "Password"):
        outcome = verify(page, _role_textbox(name))
        assert outcome.result == VerificationResult.UNIQUE, f"{name}: {outcome}"
    assert verify(page, _role_button("Sign up")).result == VerificationResult.UNIQUE


def test_settings_page_locators_resolve_uniquely_after_login(page):
    _login(page)
    page.goto(SETTINGS_URL, wait_until="networkidle")
    field_names = ["URL of profile picture", "Username", "Short bio about you", "Email", "New Password"]
    for name in field_names:
        outcome = verify(page, _role_textbox(name))
        assert outcome.result == VerificationResult.UNIQUE, f"{name}: {outcome}"
    assert verify(page, _role_button("Update Settings")).result == VerificationResult.UNIQUE


def test_editor_page_locators_resolve_uniquely_after_login(page):
    _login(page)
    page.goto(EDITOR_URL, wait_until="networkidle")
    field_names = ["Article Title", "What's this article about?", "Write your article (in markdown)", "Enter tags"]
    for name in field_names:
        outcome = verify(page, _role_textbox(name))
        assert outcome.result == VerificationResult.UNIQUE, f"{name}: {outcome}"
    assert verify(page, _role_button("Publish Article")).result == VerificationResult.UNIQUE


def test_inspect_page_falls_through_to_roles_when_no_test_ids_or_labels(page):
    page.goto(LOGIN_URL, wait_until="networkidle")
    ctx = inspect_page(page)
    aria = ctx.aria_snapshots[""]
    assert 'textbox "Email"' in aria
    assert 'textbox "Password"' in aria
    assert 'button "Sign in"' in aria
    # This real Angular app has no test-id attributes anywhere - unlike the
    # synthetic fixtures, the tool must rely entirely on the ARIA tree here.
    assert ctx.pruned_html[""] == ""


class _RealWorldLoginProvider(LLMProvider):
    """Stands in for a real LLM call, proposing the same role-based locators
    a model would derive from the ARIA snapshot asserted above."""

    def propose(self, system: str, user: str, schema: dict) -> dict:
        return {
            "elements": [
                {"name": "email_input", "locator": {"strategy": "role", "value": "textbox", "role_name": "Email"}},
                {"name": "password_input", "locator": {"strategy": "role", "value": "textbox", "role_name": "Password"}},
                {"name": "sign_in_button", "locator": {"strategy": "role", "value": "button", "role_name": "Sign in"}},
            ]
        }


def _exec_generated(code: str, path: str) -> type:
    """Execute generated code and return its POM class, whatever it was named
    (derive_class_name uses the page title, which this SPA doesn't vary per
    route, so it won't necessarily be called "LoginPage")."""
    ast.parse(code)  # fails loudly if generation ever produces invalid Python
    class_name = re.search(r"^class (\w+):", code, re.MULTILINE).group(1)
    namespace: dict = {}
    exec(compile(code, path, "exec"), namespace)
    return namespace[class_name]


def _invoke_cli_in_new_thread(args: list[str]):
    """Run the Typer CLI in a fresh OS thread. cli.py opens its own
    sync_playwright() context; Playwright's sync API forbids a second,
    nested sync_playwright() in a thread that already has one open (as the
    session-scoped `browser` fixture does here), so it must run elsewhere."""
    result_box: dict = {}

    def _target() -> None:
        result_box["result"] = CliRunner().invoke(app, args)

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    return result_box["result"]


def test_programmatic_generation_produces_a_working_login_page(page):
    page.goto(LOGIN_URL, wait_until="networkidle")
    out_path = PAGES_DIR / "login_page.py"

    agent = object.__new__(POMAgent)
    agent.max_locator_retries = 3
    agent._llm = _RealWorldLoginProvider()
    agent._warned = True

    code = agent.generate(page, out_path=str(out_path))
    LoginPage = _exec_generated(code, str(out_path))

    pom = LoginPage(page)
    pom.email_input.fill(TEST_EMAIL)
    pom.password_input.fill(TEST_PASSWORD)
    with page.expect_response(lambda r: r.url.endswith("/login") and r.request.method == "POST", timeout=15000):
        pom.sign_in_button.click()
    page.wait_for_url(f"{BASE_URL}/", timeout=15000)
    assert page.url == f"{BASE_URL}/"


def test_cli_generation_produces_a_working_login_page(page):
    out_path = PAGES_DIR / "cli_login_page.py"
    with patch("playwright_pom_agent.agent._build_provider", return_value=_RealWorldLoginProvider()):
        result = _invoke_cli_in_new_thread(["generate", "--url", LOGIN_URL, "--out", str(out_path)])
    assert result.exit_code == 0, result.output

    LoginPage = _exec_generated(out_path.read_text(), str(out_path))

    page.goto(LOGIN_URL, wait_until="networkidle")
    pom = LoginPage(page)
    pom.email_input.fill(TEST_EMAIL)
    pom.password_input.fill(TEST_PASSWORD)
    with page.expect_response(lambda r: r.url.endswith("/login") and r.request.method == "POST", timeout=15000):
        pom.sign_in_button.click()
    page.wait_for_url(f"{BASE_URL}/", timeout=15000)
    assert page.url == f"{BASE_URL}/"


def _live_llm_provider() -> str | None:
    # Gated behind an explicit opt-in env var, not just the presence of an
    # API key - ANTHROPIC_API_KEY/OPENAI_API_KEY are commonly set ambiently
    # (e.g. by an agent harness running these tests) for unrelated purposes,
    # and this test makes a real, billed LLM call.
    if not os.environ.get("RUN_LIVE_LLM_TESTS"):
        return None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401

            return "anthropic"
        except ImportError:
            return None
    if os.environ.get("OPENAI_API_KEY"):
        try:
            import openai  # noqa: F401

            return "openai"
        except ImportError:
            return None
    return None


_LIVE_LLM_PROVIDER = _live_llm_provider()


@pytest.mark.skipif(
    _LIVE_LLM_PROVIDER is None,
    reason="set RUN_LIVE_LLM_TESTS=1, a provider API key, and install that provider's SDK to run this real-LLM smoke test",
)
def test_real_llm_generates_a_working_login_page(page):
    """End-to-end smoke test with an actual LLM call. This is the reference
    for anyone wiring up CI with real credentials - run it explicitly via:
    RUN_LIVE_LLM_TESTS=1 ANTHROPIC_API_KEY=... pytest tests/test_demo/test_demo_url.py -k real_llm
    """
    page.goto(LOGIN_URL, wait_until="networkidle")
    out_path = PAGES_DIR / "real_llm_login_page.py"

    pom_agent = POMAgent(provider=_LIVE_LLM_PROVIDER)
    code = pom_agent.generate(page, out_path=str(out_path))
    LoginPage = _exec_generated(code, str(out_path))

    pom = LoginPage(page)
    assert pom.page is page
