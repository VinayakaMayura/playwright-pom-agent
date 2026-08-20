from playwright_pom_agent.locator_spec import LocatorSpec
from playwright_pom_agent.models import VerificationResult
from playwright_pom_agent.verifier import verify

from conftest import fixture_url


def test_unique_locator(page):
    page.goto(fixture_url("login.html"))
    outcome = verify(page, LocatorSpec(strategy="test_id", value="submit-form"))
    assert outcome.result == VerificationResult.UNIQUE
    assert outcome.count == 1


def test_not_found_locator(page):
    page.goto(fixture_url("login.html"))
    outcome = verify(page, LocatorSpec(strategy="test_id", value="does-not-exist"))
    assert outcome.result == VerificationResult.NOT_FOUND
    assert outcome.count == 0


def test_ambiguous_locator_resolved_with_filter(page):
    page.goto(fixture_url("login.html"))
    # get_by_text("Remember me") matches both the <label> text node and the
    # bare text run inside it in some engines' text matching; simulate an
    # ambiguous case with role text, then refine with filter_has_text.
    ambiguous = LocatorSpec(strategy="css", value="input, label")
    outcome = verify(page, ambiguous)
    assert outcome.result == VerificationResult.AMBIGUOUS
    assert outcome.count > 1

    refined = LocatorSpec(strategy="css", value="input, label", filter_has_text="Remember me")
    refined_outcome = verify(page, refined)
    assert refined_outcome.result == VerificationResult.UNIQUE


def test_iframe_locator(page):
    page.goto(fixture_url("iframe_host.html"))
    outcome = verify(page, LocatorSpec(strategy="test_id", value="pay-now", frame_selector="#payment-frame"))
    assert outcome.result == VerificationResult.UNIQUE
