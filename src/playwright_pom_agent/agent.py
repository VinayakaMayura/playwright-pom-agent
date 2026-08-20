"""Orchestrates the LLM proposal / live-verification feedback loop.

Uses the Anthropic or OpenAI SDK directly with native tool-calling /
structured outputs - no agent framework (LangChain, the `pydantic-ai`
package) - to keep the dependency footprint light. Plain `pydantic` is still
used package-wide for schema validation; that's unrelated to `pydantic-ai`.
The model never emits raw Python: it proposes a
structured ``LocatorSpec`` per element, which is what actually gets executed
against the page (see verifier.py) and rendered to source (see
locator_spec.render_locator_code). This also means the loop can't be tricked
into running arbitrary model-authored code.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel
from playwright.sync_api import Page

from .generator import derive_class_name, render_pom
from .inspector import inspect_page
from .models import DOMContext, ProposedElement, VerificationResult, VerifiedElement
from .verifier import verify

PII_WARNING = (
    "[playwright-pom-agent] Sending this page's accessibility tree (roles, "
    "visible text, structure) and a small test-id attribute inventory to the "
    "configured LLM provider to propose locators. No cookies, storage, or "
    "form field values are sent. If the page visibly displays sensitive info "
    "(names, account numbers, etc.), that text is included - review before "
    "running against such pages."
)


class ProposalBatch(BaseModel):
    elements: list[ProposedElement]


PROPOSAL_SCHEMA = ProposalBatch.model_json_schema()


class LLMProvider:
    def propose(self, system: str, user: str, schema: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str = "claude-sonnet-5", api_key: Optional[str] = None):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def propose(self, system: str, user: str, schema: dict) -> dict:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            tools=[
                {
                    "name": "propose_elements",
                    "description": "Propose verified-candidate locators for interactive page elements.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": "propose_elements"},
            messages=[{"role": "user", "content": user}],
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError("Model response did not include a tool call")


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str = "gpt-4o", api_key: Optional[str] = None):
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    def propose(self, system: str, user: str, schema: dict) -> dict:
        import json

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "propose_elements",
                        "description": "Propose verified-candidate locators for interactive page elements.",
                        "parameters": schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "propose_elements"}},
        )
        call = response.choices[0].message.tool_calls[0]
        return json.loads(call.function.arguments)


def _build_provider(provider: Literal["anthropic", "openai"], model: Optional[str], api_key: Optional[str]) -> LLMProvider:
    if provider == "anthropic":
        return AnthropicProvider(model=model or "claude-sonnet-5", api_key=api_key)
    if provider == "openai":
        return OpenAIProvider(model=model or "gpt-4o", api_key=api_key)
    raise ValueError(f"Unknown provider: {provider!r}")


_SYSTEM_PROMPT = """You generate Playwright Python locators for a Page Object Model.

Strictly rank candidate locators in this order, using the highest tier that \
applies to each element - never skip to a lower tier if a higher one is valid:
1. test_id      - element has a "{test_id_attribute}" (or data-pw/data-qa) attribute
2. role         - get_by_role, with an accessible name when one exists
3. label        - get_by_label, for form fields with a linked <label>
4. placeholder  - get_by_placeholder, only if no label exists
5. text         - get_by_text, for non-interactive text containers
6. alt_text/title - for images and titled elements
7. css_id       - "#id", only if nothing above yields a unique match
8. css_name     - "[name=...]", only if css_id is unavailable
9. css / xpath  - last resort only

When told an element is ambiguous (count > 1) or not found (count == 0), fix \
ONLY that element: prefer adding filter_has_text or a parent_css scope over \
jumping to a lower tier. Return the full corrected element in your response."""


def _build_user_prompt(dom_context: DOMContext) -> str:
    parts = ["Propose locators for every interactive element on this page.\n"]
    for frame in dom_context.frames:
        label = "main frame" if not frame.frame_selector else f"frame ({frame.frame_selector})"
        parts.append(f"--- {label}: {frame.url} ---")
        parts.append("ARIA snapshot:\n" + dom_context.aria_snapshots.get(frame.frame_selector, ""))
        test_ids = dom_context.pruned_html.get(frame.frame_selector, "")
        if test_ids:
            parts.append("Elements with test-id attributes:\n" + test_ids)
        if frame.frame_selector:
            parts.append(f'Set frame_selector={frame.frame_selector!r} on every LocatorSpec from this frame.')
    for group in dom_context.form_groups:
        field_names = ", ".join(f.element_name for f in group.fields)
        parts.append(
            f"Detected form group '{group.name}': fields=[{field_names}] submit={group.submit_element_name}. "
            "Name the corresponding proposed elements after these exact hints (snake_case) so they can be "
            "wired into a generated helper method."
        )
    return "\n\n".join(parts)


class POMAgent:
    def __init__(
        self,
        provider: Literal["anthropic", "openai"] = "anthropic",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        max_locator_retries: int = 3,
    ):
        self.max_locator_retries = max_locator_retries
        self._llm = _build_provider(provider, model, api_key)
        self._warned = False

    def _warn_pii(self) -> None:
        if not self._warned:
            print(PII_WARNING, file=sys.stderr)
            self._warned = True

    def generate(self, page: Page, out_path: Optional[str] = None, test_id_attribute: str = "data-testid") -> str:
        self._warn_pii()
        dom_context = inspect_page(page, test_id_attribute=test_id_attribute)
        verified = self.resolve_elements(page, dom_context)
        class_name = derive_class_name(page.title(), page.url)
        code = render_pom(class_name, verified, dom_context.form_groups)
        if out_path:
            Path(out_path).write_text(code)
        return code

    def resolve_elements(self, page: Page, dom_context: DOMContext) -> list[VerifiedElement]:
        system = _SYSTEM_PROMPT.format(test_id_attribute=dom_context.test_id_attribute)
        base_prompt = _build_user_prompt(dom_context)

        resolved: dict[str, VerifiedElement] = {}
        last_seen: dict[str, ProposedElement] = {}
        feedback: Optional[str] = None

        for attempt in range(1, self.max_locator_retries + 1):
            prompt = base_prompt if feedback is None else f"{base_prompt}\n\n{feedback}"
            raw = self._llm.propose(system, prompt, PROPOSAL_SCHEMA)
            batch = ProposalBatch.model_validate(raw)

            unresolved_feedback: list[str] = []
            for element in batch.elements:
                if element.name in resolved:
                    continue
                last_seen[element.name] = element
                outcome = verify(page, element.locator)
                if outcome.result == VerificationResult.UNIQUE:
                    resolved[element.name] = VerifiedElement(
                        name=element.name, locator=element.locator, tier=element.locator.tier,
                        status=outcome.result, attempts=attempt,
                    )
                else:
                    unresolved_feedback.append(
                        f"- {element.name}: strategy={element.locator.strategy} value={element.locator.value!r} "
                        f"-> {outcome.result.value} (count={outcome.count})"
                    )

            if not unresolved_feedback:
                break
            feedback = (
                "These elements did not resolve uniquely; fix only these and resend them "
                "(refine with filter_has_text or parent_css before changing tier):\n"
                + "\n".join(unresolved_feedback)
            )

        # Anything still unresolved after max_locator_retries is emitted anyway,
        # flagged, rather than looping forever - see generator's UNVERIFIED comment.
        for name, element in last_seen.items():
            if name not in resolved:
                outcome = verify(page, element.locator)
                resolved[name] = VerifiedElement(
                    name=name, locator=element.locator, tier=element.locator.tier,
                    status=outcome.result, attempts=self.max_locator_retries,
                )

        return list(resolved.values())
