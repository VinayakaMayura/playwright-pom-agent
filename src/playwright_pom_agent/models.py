from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel

from .locator_spec import LocatorSpec, LocatorTier

InputKind = Literal["text", "email", "password", "checkbox", "radio", "other"]


class FrameInfo(BaseModel):
    """A frame in the page, addressed by the CSS selector of its <iframe>
    element in its parent (empty string for the main frame)."""

    frame_selector: str
    url: str


class FormGroupField(BaseModel):
    element_name: str
    input_kind: InputKind = "text"


class FormGroup(BaseModel):
    name: str
    frame_selector: str = ""
    fields: list[FormGroupField]
    submit_element_name: Optional[str] = None
    is_login: bool = False


class DOMContext(BaseModel):
    """Pruned, LLM-facing representation of a page's interactive state."""

    frames: list[FrameInfo]
    aria_snapshots: dict[str, str]  # frame_selector -> YAML ARIA snapshot
    pruned_html: dict[str, str]  # frame_selector -> pruned HTML (for test-id discovery)
    form_groups: list[FormGroup]
    test_id_attribute: str = "data-testid"


class VerificationResult(str, Enum):
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class ProposedElement(BaseModel):
    """One element proposed by the LLM, prior to verification."""

    name: str
    locator: LocatorSpec


class VerifiedElement(BaseModel):
    name: str
    locator: LocatorSpec
    tier: LocatorTier
    status: VerificationResult
    attempts: int = 1

    @property
    def code(self) -> str:
        from .locator_spec import render_locator_code

        return render_locator_code(self.locator)
