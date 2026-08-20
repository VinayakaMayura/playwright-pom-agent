"""Extracts an LLM-facing, token-lean snapshot of a page's interactive state.

Uses Playwright's YAML ARIA snapshot (``locator.aria_snapshot()``) as the
primary structural representation instead of the legacy
``page.accessibility.snapshot()`` API. ARIA snapshots already fold in
accessible names (which cover roles, labels, placeholders-as-name, alt text,
and titles), so the only thing they don't carry is test-id attributes -
those are extracted separately from a small pruned-HTML inventory.
"""
from __future__ import annotations

from bs4 import BeautifulSoup, Comment
from playwright.sync_api import Frame, Page

from .models import DOMContext, FormGroup, FormGroupField, FrameInfo

DEFAULT_TEST_ID_ATTRIBUTES = ["data-testid", "data-pw", "data-qa", "data-test"]

_SUBMIT_WORDS = ("submit", "login", "log in", "sign in", "signin", "save", "continue", "register", "sign up")


def _frame_selector_for(element_handle, index: int) -> str:
    el_id = element_handle.get_attribute("id")
    if el_id:
        return f"#{el_id}"
    name = element_handle.get_attribute("name")
    if name:
        return f'iframe[name="{name}"]'
    src = element_handle.get_attribute("src")
    if src:
        return f'iframe[src="{src}"]'
    return f"iframe >> nth={index}"


def _extract_test_id_inventory(soup: BeautifulSoup, attributes: list[str]) -> str:
    lines = []
    for attr in attributes:
        for el in soup.find_all(attrs={attr: True}):
            text = " ".join(el.stripped_strings)[:80]
            lines.append(f'<{el.name} {attr}="{el[attr]}"> {text!r}')
    return "\n".join(lines)


def _detect_input_kind(tag) -> str:
    if tag.name == "select":
        return "other"
    input_type = (tag.get("type") or "text").lower()
    if input_type in ("checkbox", "radio", "password", "email"):
        return input_type
    return "text"


def _element_name_hint(tag) -> str:
    for attr in DEFAULT_TEST_ID_ATTRIBUTES:
        if tag.get(attr):
            return tag[attr]
    return tag.get("name") or tag.get("id") or tag.name


def _detect_form_groups(soup: BeautifulSoup, frame_selector: str) -> list[FormGroup]:
    groups: list[FormGroup] = []

    containers = soup.find_all("form")
    if not containers:
        # No <form> tag: fall back to the smallest container holding >=2
        # related inputs plus a submit-like control.
        for container in soup.find_all(["div", "section"]):
            inputs = container.find_all(["input", "select", "textarea"], recursive=False)
            if len(inputs) < 2:
                continue
            has_submit = container.find(
                lambda t: t.name in ("button", "input")
                and any(w in " ".join(t.stripped_strings).lower() + (t.get("value") or "").lower() for w in _SUBMIT_WORDS)
            )
            if has_submit:
                containers.append(container)

    for idx, container in enumerate(containers):
        inputs = [
            t
            for t in container.find_all(["input", "select", "textarea"])
            if (t.get("type") or "text").lower() not in ("submit", "hidden", "button")
        ]
        if not inputs:
            continue
        submit_tag = container.find(
            lambda t: (t.name == "button" and t.get("type", "submit") != "button")
            or (t.name == "input" and (t.get("type") or "").lower() == "submit")
        )
        fields = [FormGroupField(element_name=_element_name_hint(t), input_kind=_detect_input_kind(t)) for t in inputs]
        is_login = any(f.input_kind == "password" for f in fields)
        name = "login" if is_login else f"form_{idx}"
        groups.append(
            FormGroup(
                name=name,
                frame_selector=frame_selector,
                fields=fields,
                submit_element_name=_element_name_hint(submit_tag) if submit_tag is not None else None,
                is_login=is_login,
            )
        )
    return groups


def _clean_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    for svg in soup.find_all("svg"):
        svg.decompose()
    return soup


def _inspect_frame(frame: Frame, frame_selector: str, test_id_attributes: list[str]) -> tuple[str, str, list[FormGroup]]:
    html = frame.content()
    soup = _clean_soup(html)
    aria_snapshot = frame.locator("body").aria_snapshot()
    test_id_inventory = _extract_test_id_inventory(soup, test_id_attributes)
    form_groups = _detect_form_groups(soup, frame_selector)
    return aria_snapshot, test_id_inventory, form_groups


def inspect_page(page: Page, test_id_attribute: str = "data-testid") -> DOMContext:
    """Build a ``DOMContext`` from the current, live state of ``page``.

    Only the current DOM state is captured - content behind an interaction
    (a closed modal, an unopened dropdown) is invisible here. Drive the page
    into the state you want documented (via a ``--setup-script`` for the CLI,
    or by acting on ``page`` yourself before calling ``POMAgent.generate``)
    before calling this.
    """
    test_id_attributes = [test_id_attribute] + [a for a in DEFAULT_TEST_ID_ATTRIBUTES if a != test_id_attribute]

    frames_info: list[FrameInfo] = []
    aria_snapshots: dict[str, str] = {}
    pruned_html: dict[str, str] = {}
    form_groups: list[FormGroup] = []

    main_aria, main_inventory, main_groups = _inspect_frame(page.main_frame, "", test_id_attributes)
    frames_info.append(FrameInfo(frame_selector="", url=page.url))
    aria_snapshots[""] = main_aria
    pruned_html[""] = main_inventory
    form_groups.extend(main_groups)

    # Walk Playwright's own frame tree (rather than parsing <iframe> tags out
    # of HTML) so selectors are derived from the live DOM element, not from
    # possibly-relative src attributes that won't string-match frame.url.
    for idx, child_frame in enumerate(page.main_frame.child_frames):
        try:
            element_handle = child_frame.frame_element()
            frame_selector = _frame_selector_for(element_handle, idx)
            aria, inventory, groups = _inspect_frame(child_frame, frame_selector, test_id_attributes)
        except Exception:
            continue  # cross-origin or detached frame; skip rather than fail the whole snapshot
        frames_info.append(FrameInfo(frame_selector=frame_selector, url=child_frame.url))
        aria_snapshots[frame_selector] = aria
        pruned_html[frame_selector] = inventory
        form_groups.extend(groups)

    return DOMContext(
        frames=frames_info,
        aria_snapshots=aria_snapshots,
        pruned_html=pruned_html,
        form_groups=form_groups,
        test_id_attribute=test_id_attribute,
    )
