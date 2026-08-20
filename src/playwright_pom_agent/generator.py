"""Renders verified elements into a Playwright Python POM class."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader

from .models import FormGroup, VerificationResult, VerifiedElement

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), trim_blocks=True, lstrip_blocks=True)


def _to_snake_case(name: str) -> str:
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    name = name.lower() or "element"
    if name[0].isdigit():
        name = f"_{name}"
    return name


def _dedupe_names(elements: list[VerifiedElement]) -> dict[str, str]:
    """Map each element's original name to a unique, valid Python identifier."""
    seen: dict[str, int] = {}
    mapping: dict[str, str] = {}
    for element in elements:
        base = _to_snake_case(element.name)
        count = seen.get(base, 0)
        attr_name = base if count == 0 else f"{base}_{count}"
        seen[base] = count + 1
        mapping[element.name] = attr_name
    return mapping


def derive_class_name(title: str, url: str) -> str:
    source = title.strip() if title else ""
    if not source and url:
        path = urlparse(url).path.strip("/")
        source = path.rsplit("/", 1)[-1] if path else urlparse(url).netloc
    words = re.sub(r"[^0-9a-zA-Z]+", " ", source).split()
    words = [w.capitalize() for w in words if w] or ["Page"]
    name = "".join(words)
    if not name.endswith("Page"):
        name += "Page"
    return name


def _status_comment(element: VerifiedElement) -> str | None:
    if element.status == VerificationResult.UNIQUE:
        return None
    reason = "ambiguous match (count > 1)" if element.status == VerificationResult.AMBIGUOUS else "not found (count == 0)"
    return f"UNVERIFIED: {reason} after {element.attempts} attempt(s) - review before use."


def _form_group_method(group: FormGroup, attr_names: dict[str, str]) -> dict | None:
    # Elements are matched to form-group fields by normalized (snake_case) name,
    # since the LLM names elements independently of the raw DOM hint the
    # inspector recorded on each field (e.g. "email-input" vs "email_input").
    by_normalized = {_to_snake_case(orig): attr for orig, attr in attr_names.items()}

    submit_attr = by_normalized.get(_to_snake_case(group.submit_element_name or ""))
    if submit_attr is None:
        return None

    resolved_fields = [(f, by_normalized.get(_to_snake_case(f.element_name))) for f in group.fields]
    resolved_fields = [(f, attr) for f, attr in resolved_fields if attr is not None]
    if not resolved_fields:
        return None

    params = []
    body = []
    for field, attr in resolved_fields:
        if field.input_kind in ("checkbox", "radio"):
            params.append({"name": attr, "type": "bool"})
            body.append(f"self.{attr}.set_checked({attr})")
        else:
            params.append({"name": attr, "type": "str"})
            body.append(f"self.{attr}.fill({attr})")
    body.append(f"self.{submit_attr}.click()")
    return {"name": _to_snake_case(group.name), "params": params, "body": body}


def render_pom(class_name: str, elements: list[VerifiedElement], form_groups: list[FormGroup]) -> str:
    attr_names = _dedupe_names(elements)

    rendered_elements = [
        {"attr_name": attr_names[el.name], "code": el.code, "comment": _status_comment(el)} for el in elements
    ]

    methods = [m for g in form_groups if (m := _form_group_method(g, attr_names)) is not None]

    template = _env.get_template("pom_class.py.jinja")
    return template.render(class_name=class_name, elements=rendered_elements, methods=methods)
