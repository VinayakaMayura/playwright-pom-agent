"""Single source of truth for turning a proposed locator into both a live
Playwright ``Locator`` (for verification) and Python source code (for the
generated POM). Keeping both derived from the same steps guarantees that
whatever was verified is exactly what gets written to disk.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Literal, Optional

from pydantic import BaseModel

LocatorStrategy = Literal[
    "test_id",
    "role",
    "label",
    "placeholder",
    "text",
    "alt_text",
    "title",
    "css_id",
    "css_name",
    "css",
    "xpath",
]


class LocatorTier(IntEnum):
    TEST_ID = 1
    ROLE = 2
    LABEL = 3
    PLACEHOLDER = 4
    TEXT = 5
    ALT_OR_TITLE = 6
    CSS_ID_OR_NAME = 7
    CSS_OR_XPATH = 8


_STRATEGY_TIER = {
    "test_id": LocatorTier.TEST_ID,
    "role": LocatorTier.ROLE,
    "label": LocatorTier.LABEL,
    "placeholder": LocatorTier.PLACEHOLDER,
    "text": LocatorTier.TEXT,
    "alt_text": LocatorTier.ALT_OR_TITLE,
    "title": LocatorTier.ALT_OR_TITLE,
    "css_id": LocatorTier.CSS_ID_OR_NAME,
    "css_name": LocatorTier.CSS_ID_OR_NAME,
    "css": LocatorTier.CSS_OR_XPATH,
    "xpath": LocatorTier.CSS_OR_XPATH,
}


class LocatorSpec(BaseModel):
    """A candidate locator proposed by the agent, not yet verified."""

    strategy: LocatorStrategy
    value: str
    attribute: Optional[str] = None  # test-id attribute name, e.g. "data-testid"
    role_name: Optional[str] = None  # accessible name for get_by_role
    exact: bool = False
    filter_has_text: Optional[str] = None  # ambiguity refinement
    parent_css: Optional[str] = None  # parent scoping for ambiguity refinement
    frame_selector: Optional[str] = None  # iframe selector, if element is framed

    @property
    def tier(self) -> LocatorTier:
        return _STRATEGY_TIER[self.strategy]


def _steps(spec: LocatorSpec) -> list[tuple[str, list, dict]]:
    steps: list[tuple[str, list, dict]] = []
    if spec.parent_css:
        steps.append(("locator", [spec.parent_css], {}))

    exact_kwargs = {"exact": True} if spec.exact else {}

    if spec.strategy == "test_id":
        attr = spec.attribute or "data-testid"
        if attr == "data-testid":
            steps.append(("get_by_test_id", [spec.value], {}))
        else:
            # get_by_test_id() only matches the attribute configured globally via
            # playwright.selectors.set_test_id_attribute(); we can't rely on that
            # being set from just a Page, so emit the equivalent CSS attribute
            # match instead. Still tier-1: it targets an explicit test hook.
            steps.append(("locator", [f'[{attr}="{spec.value}"]'], {}))
    elif spec.strategy == "role":
        kwargs = dict(exact_kwargs)
        if spec.role_name is not None:
            kwargs["name"] = spec.role_name
        steps.append(("get_by_role", [spec.value], kwargs))
    elif spec.strategy == "label":
        steps.append(("get_by_label", [spec.value], exact_kwargs))
    elif spec.strategy == "placeholder":
        steps.append(("get_by_placeholder", [spec.value], exact_kwargs))
    elif spec.strategy == "text":
        steps.append(("get_by_text", [spec.value], exact_kwargs))
    elif spec.strategy == "alt_text":
        steps.append(("get_by_alt_text", [spec.value], exact_kwargs))
    elif spec.strategy == "title":
        steps.append(("get_by_title", [spec.value], exact_kwargs))
    elif spec.strategy == "css_id":
        steps.append(("locator", [f"#{spec.value}"], {}))
    elif spec.strategy == "css_name":
        steps.append(("locator", [f'[name="{spec.value}"]'], {}))
    elif spec.strategy == "css":
        steps.append(("locator", [spec.value], {}))
    elif spec.strategy == "xpath":
        steps.append(("locator", [f"xpath={spec.value}"], {}))
    else:  # pragma: no cover - guarded by Literal typing
        raise ValueError(f"Unknown locator strategy: {spec.strategy}")

    if spec.filter_has_text:
        steps.append(("filter", [], {"has_text": spec.filter_has_text}))

    return steps


def build_locator(root, spec: LocatorSpec):
    """Resolve ``spec`` into a live Playwright Locator, starting from
    ``root`` (a Page, Frame, FrameLocator, or Locator)."""
    obj = root
    for method, args, kwargs in _steps(spec):
        obj = getattr(obj, method)(*args, **kwargs)
    return obj


def render_locator_code(spec: LocatorSpec, root_expr: str = "self.page") -> str:
    """Render the exact Python source for ``spec``, chained off ``root_expr``."""
    if spec.frame_selector:
        root_expr = f"{root_expr}.frame_locator({spec.frame_selector!r})"
    code = root_expr
    for method, args, kwargs in _steps(spec):
        arg_strs = [repr(a) for a in args]
        kwarg_strs = [f"{k}={v!r}" for k, v in kwargs.items()]
        code += f".{method}({', '.join(arg_strs + kwarg_strs)})"
    return code
