# playwright-pom-agent

Generates Playwright Python Page Object Model classes from a live page,
using an LLM to propose locators and a live verification loop to make sure
every locator it writes is actually unique on the page before it's used.

## Data sent to the LLM provider

Generation sends the page's accessibility tree (roles, visible text,
structure) and a small test-id attribute inventory (tag + attribute value +
a short text snippet) to your configured LLM provider (Anthropic or OpenAI).
It does **NOT** read cookies, local/session storage, or form field values.
If a page visibly displays sensitive info (names, account numbers, etc.),
that text is included in what's sent - review before running against such
pages. The CLI prints this notice on every run; it doesn't block execution.

## Locator priority

1. `get_by_test_id()` (`data-testid`, or a custom attribute via `--test-id-attribute`)
2. `get_by_role()`
3. `get_by_label()`
4. `get_by_placeholder()`
5. `get_by_text()`
6. `get_by_alt_text()` / `get_by_title()`
7. `#id` CSS selector
8. `[name="..."]` CSS selector
9. Generic CSS / XPath (last resort)

Every candidate is verified with `.count() == 1` on the live page before
being written to the generated file. Ambiguous or unresolved elements are
retried (default: 3 attempts, via `.filter(has_text=...)` or parent scoping)
before being emitted with an `# UNVERIFIED` comment rather than looping
forever.

## Dynamic UI state (modals, dropdowns, accordions)

Only the DOM present at inspection time is visible. This tool does not
auto-crawl interactions. To document elements behind an interaction:

- **CLI**: pass `--setup-script path/to/setup.py`, a file defining
  `def setup(page): ...` that runs after navigation and before inspection
  (e.g. click to open a modal).
- **Programmatic API**: drive `page` into the desired state yourself before
  calling `POMAgent.generate(page)`.

## Generated files

Generated POM files start with an `# AUTO-GENERATED ... DO NOT EDIT` header.
Re-running generation overwrites the file completely - put custom logic in a
subclass, not in the generated file.

## CLI

Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (matching `--provider`) before
running - the CLI also accepts `--api-key` directly, but the env var is
preferred since CLI arguments can leak via shell history and process listings.

```bash
export ANTHROPIC_API_KEY=sk-...
pom-agent generate --url https://example.com/login \
  --out example_login_page.py \
  --test-id-attribute data-pw \
  --provider anthropic
```

## Programmatic

```python
from playwright.sync_api import sync_playwright
from playwright_pom_agent import POMAgent

with sync_playwright() as p:
    page = p.chromium.launch().new_page()
    page.goto("https://example.com/login")

    agent = POMAgent(provider="anthropic")
    code = agent.generate(page, out_path="login_page.py")
```

## Testing

```bash
uv pip install -e ".[dev]"
pytest tests/
```

`tests/test_fixtures/` runs against local HTML fixtures in `tests/fixtures/`.
`tests/test_demo/test_demo_url.py` is a reference suite that exercises the
whole tool against a real site - https://demo.realworld.show/, a public
"Conduit" demo app with no `data-testid`s or `<label>`s anywhere, so it
genuinely tests the role/placeholder fallback tiers instead of the tier-1
shortcut most fixtures hit. It covers locator resolution across the login,
register, settings, and editor pages, plus full programmatic and CLI
generation runs that actually log in with the generated code - those runs
write their generated POM classes to `tests/test_demo/pages/` (overwritten
on each run) so you can open real, working, generated output rather than
just reading assertions. Use it as a template for pointing this tool at your
own app (swap `BASE_URL`/`TEST_EMAIL`/`TEST_PASSWORD`, or set the
`REALWORLD_*` env vars it reads).

These tests are marked `live` and require internet access:

```bash
pytest tests/ -m "not live"   # skip the live/network tests
pytest tests/ -m live         # run only the live tests
```

One test in that file makes a real, billed LLM call and is opt-in only -
it won't run just because `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` happen to be
set in your shell:

```bash
RUN_LIVE_LLM_TESTS=1 ANTHROPIC_API_KEY=sk-... pytest tests/test_demo/test_demo_url.py -k real_llm
```
