from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


def fixture_url(name: str) -> str:
    return f"file://{(FIXTURES / name).resolve()}"
