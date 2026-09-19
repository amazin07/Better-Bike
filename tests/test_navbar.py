"""The fixed top navbar is one design on both pages: BikeBetter (Toronto) on the left,
Map / Rentals in the centre, and sign in or out on the right.

The markup lives in both pages, so these tests keep the two copies identical and make
sure the pages leave room for the bar. Nothing here touches the network.
"""
import re
from pathlib import Path

import pytest

from app import create_app
from traffic import TrafficCache

ROOT = Path(__file__).resolve().parent.parent
PAGES = {"map": "index.html", "rentals": "rentals.html"}


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def navbar(html):
    match = re.search(r'<header class="site-nav">.*?</header>', html, re.S)
    assert match, "the page has no shared navbar"
    return match.group(0)


def comparable(markup):
    """Whitespace and the current-page marker are the only things allowed to differ."""
    markup = re.sub(r'\s*aria-current="page"', "", markup)
    markup = re.sub(r"\s+", " ", markup)
    markup = re.sub(r"\s+>", ">", markup)
    return re.sub(r">\s+<", "><", markup).strip()


@pytest.fixture(scope="module")
def pages():
    return {name: read(file) for name, file in PAGES.items()}


def test_both_pages_use_the_same_navbar_markup(pages):
    assert comparable(navbar(pages["map"])) == comparable(navbar(pages["rentals"]))


@pytest.mark.parametrize("page,current", [("map", "Map"), ("rentals", "Rentals")])
def test_each_page_marks_only_its_own_link_as_current(pages, page, current):
    links = re.findall(r'<a href="(/[a-z]*)"(\s+aria-current="page")?\s*>\s*([^<]+?)\s*</a\s*>',
                       navbar(pages[page]))
    assert [(href, label) for href, _, label in links] == [
        ("/", "Map"), ("/rentals", "Rentals"), ("/missing", "Missing bikes")]
    assert [label for _, mark, label in links if mark] == [current]


def test_the_navbar_has_the_brand_city_links_and_account_controls(pages):
    for html in pages.values():
        bar = navbar(html)
        assert re.search(r'site-brand-name">\s*BikeBetter\s*<', bar)
        assert re.search(r'site-city">\s*Toronto\s*<', bar)
        assert 'aria-label="Main"' in bar
        assert 'id="account-name"' in bar and 'id="auth-button"' in bar


def test_the_navbar_is_fixed_to_the_top_of_the_window():
    css = read("static", "navbar.css")
    block = re.search(r"\.site-nav\s*\{([^}]*)\}", css).group(1)
    assert re.search(r"position:\s*fixed", block) and re.search(r"top:\s*0", block)
    assert re.search(r"--nav-h:\s*\d+px", css)


def test_both_pages_load_the_shared_stylesheet(pages):
    for html in pages.values():
        assert 'href="/static/navbar.css"' in html


def test_the_map_page_sits_below_the_fixed_bar(pages):
    html = pages["map"]
    assert "var(--nav-h)" in re.search(r"#map\s*\{([^}]*)\}", html).group(1)
    for selector in (r"\.controls", r"\.comparison"):
        assert "var(--nav-h)" in re.search(selector + r"\s*\{([^}]*)\}", html).group(1)
    assert "page-nav" not in html, "the old floating nav must be gone"


def test_the_map_page_wires_sign_in_and_rentals_keeps_its_own(pages):
    assert re.search(r'<script[^>]+type="module"[^>]+src="/static/nav-auth\.js"', pages["map"])
    assert "nav-auth.js" not in pages["rentals"]          # rentals.js wires the same button itself


def test_both_sign_in_scripts_use_the_same_wording():
    for name in ("nav-auth.js", "rentals.js"):
        js = read("static", name)
        for text in ("Sign in with Google", "Sign out", "Unavailable"):
            assert text in js, f"{name} is missing {text!r}"
    assert 'getElementById("auth-button")' in read("static", "nav-auth.js")


def test_the_old_rentals_header_styles_are_gone():
    css = read("static", "rentals.css")
    for leftover in (r"^\s*(header|nav)\s*[,{]", r"^\s*\.(brand|account)\b", r"^\s*#account-name\b"):
        assert not re.search(leftover, css, re.M), f"leftover rule would fight the shared navbar: {leftover}"
    assert "var(--nav-h" in css, "rentals content must clear the fixed bar"


def test_shared_assets_are_served(tmp_path):
    client = create_app(cache_path=tmp_path / "missing.pkl", traffic=TrafficCache(None)).test_client()
    for path, kind in (("/static/navbar.css", "css"), ("/static/nav-auth.js", "javascript")):
        response = client.get(path)
        assert response.status_code == 200 and kind in response.content_type
