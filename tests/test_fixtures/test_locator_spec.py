from playwright_pom_agent.locator_spec import LocatorSpec, build_locator, render_locator_code
from playwright_pom_agent.models import VerificationResult
from playwright_pom_agent.verifier import verify

from conftest import fixture_url


def test_test_id_default_attribute_renders_get_by_test_id():
    spec = LocatorSpec(strategy="test_id", value="submit-form")
    assert render_locator_code(spec) == "self.page.get_by_test_id('submit-form')"


def test_test_id_custom_attribute_renders_css_attribute_selector():
    spec = LocatorSpec(strategy="test_id", value="submit-form", attribute="data-pw")
    assert render_locator_code(spec) == 'self.page.locator(\'[data-pw="submit-form"]\')'


def test_role_with_name_renders_kwargs():
    spec = LocatorSpec(strategy="role", value="checkbox", role_name="Remember me")
    assert render_locator_code(spec) == "self.page.get_by_role('checkbox', name='Remember me')"


def test_frame_selector_prefixes_with_frame_locator():
    spec = LocatorSpec(strategy="test_id", value="pay-now", frame_selector="#payment-frame")
    code = render_locator_code(spec)
    assert code.startswith("self.page.frame_locator('#payment-frame')")
    assert code.endswith(".get_by_test_id('pay-now')")


def test_build_locator_matches_rendered_code_semantics(page):
    page.goto(fixture_url("login.html"))
    spec = LocatorSpec(strategy="label", value="Password")
    locator = build_locator(page, spec)
    assert locator.count() == 1

    outcome = verify(page, spec)
    assert outcome.result == VerificationResult.UNIQUE
