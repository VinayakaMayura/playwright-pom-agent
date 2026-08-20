"""Runtime verification: every proposed locator is tested against the live
page before it's allowed into generated code. Locators are built from a
structured ``LocatorSpec`` (never from raw LLM-authored source), so what's
verified here is guaranteed to be identical to what ``generator.py`` writes.
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page

from .locator_spec import LocatorSpec, build_locator
from .models import VerificationResult


@dataclass
class VerificationOutcome:
    result: VerificationResult
    count: int
    visible: bool | None = None


def _resolve_root(page: Page, spec: LocatorSpec):
    return page.frame_locator(spec.frame_selector) if spec.frame_selector else page


def verify(page: Page, spec: LocatorSpec) -> VerificationOutcome:
    root = _resolve_root(page, spec)
    locator = build_locator(root, spec)
    count = locator.count()

    if count == 0:
        return VerificationOutcome(result=VerificationResult.NOT_FOUND, count=0)
    if count > 1:
        return VerificationOutcome(result=VerificationResult.AMBIGUOUS, count=count)

    visible = None
    try:
        visible = locator.is_visible()
    except Exception:
        pass  # visibility is advisory; uniqueness is what gates generation
    return VerificationOutcome(result=VerificationResult.UNIQUE, count=1, visible=visible)
