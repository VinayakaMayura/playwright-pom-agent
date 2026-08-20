from playwright_pom_agent.inspector import inspect_page

from conftest import fixture_url


def test_inspect_extracts_aria_snapshot_and_test_ids(page):
    page.goto(fixture_url("login.html"))
    ctx = inspect_page(page)

    assert "textbox" in ctx.aria_snapshots[""]
    assert 'data-testid="email-input"' in ctx.pruned_html[""]
    assert 'data-testid="submit-form"' in ctx.pruned_html[""]


def test_inspect_detects_login_form_group(page):
    page.goto(fixture_url("login.html"))
    ctx = inspect_page(page)

    assert len(ctx.form_groups) == 1
    group = ctx.form_groups[0]
    assert group.is_login is True
    assert group.submit_element_name == "submit-form"
    field_names = {f.element_name for f in group.fields}
    assert field_names == {"email-input", "password", "remember"}


def test_inspect_walks_iframes(page):
    page.goto(fixture_url("iframe_host.html"))
    ctx = inspect_page(page)

    frame_selectors = {f.frame_selector for f in ctx.frames}
    assert "" in frame_selectors
    assert "#payment-frame" in frame_selectors
    assert 'data-testid="pay-now"' in ctx.pruned_html["#payment-frame"]


def test_inspect_only_sees_currently_rendered_dom(page):
    page.goto(fixture_url("dropdown.html"))
    ctx = inspect_page(page)
    assert 'data-testid="settings-link"' not in ctx.pruned_html[""]

    page.click("#menu-toggle")
    ctx_after = inspect_page(page)
    assert 'data-testid="settings-link"' in ctx_after.pruned_html[""]
