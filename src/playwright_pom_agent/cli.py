from __future__ import annotations

import runpy
from pathlib import Path
from typing import Optional

import typer
from playwright.sync_api import sync_playwright

from .agent import POMAgent

app = typer.Typer(add_completion=False)


@app.callback()
def _main() -> None:
    """playwright-pom-agent: generate Playwright POM classes from a live page."""


def _run_setup_script(setup_script: Path, page) -> None:
    """Execute a user-provided ``def setup(page): ...`` hook before inspection,
    so callers can open modals/dropdowns/accordions - anything not present in
    the page's initial DOM - before generation runs."""
    module = runpy.run_path(str(setup_script))
    setup_fn = module.get("setup")
    if setup_fn is None:
        raise typer.BadParameter(f"{setup_script} must define a `def setup(page):` function")
    setup_fn(page)


@app.command()
def generate(
    url: str = typer.Option(..., "--url", help="URL of the page to generate a POM for"),
    out: Path = typer.Option(..., "--out", help="Output file path for the generated POM class"),
    test_id_attribute: str = typer.Option("data-testid", "--test-id-attribute", help="Custom test-id attribute, e.g. data-pw"),
    setup_script: Optional[Path] = typer.Option(
        None, "--setup-script", help="Path to a Python file defining def setup(page) to drive dynamic UI state before inspection"
    ),
    max_retries: int = typer.Option(3, "--max-retries", help="Max refinement attempts per ambiguous/not-found locator"),
    provider: str = typer.Option("anthropic", "--provider", help="LLM provider: anthropic or openai"),
    model: Optional[str] = typer.Option(None, "--model", help="Override the provider's default model"),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="LLM provider API key. Prefer ANTHROPIC_API_KEY/OPENAI_API_KEY env vars instead - "
        "CLI arguments can leak via shell history and process listings.",
    ),
) -> None:
    """Generate a Playwright Python POM class from a live page."""
    pom_agent = POMAgent(provider=provider, model=model, api_key=api_key, max_locator_retries=max_retries)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        # "networkidle" (not the default "load") because SPAs commonly render
        # their interactive content client-side, after the load event fires -
        # inspecting too early would find an empty or partial DOM.
        page.goto(url, wait_until="networkidle")

        if setup_script is not None:
            _run_setup_script(setup_script, page)

        code = pom_agent.generate(page, out_path=str(out), test_id_attribute=test_id_attribute)
        browser.close()

    typer.echo(f"Wrote {out} ({len(code.splitlines())} lines)")


if __name__ == "__main__":
    app()
