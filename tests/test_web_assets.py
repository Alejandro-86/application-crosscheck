from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "crosscheck" / "web"


def test_front_end_never_writes_untrusted_text_as_html():
    script = (WEB / "app.js").read_text(encoding="utf-8")
    for dangerous in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert dangerous not in script, dangerous


def test_page_references_its_assets_and_every_script_target_exists():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    script = (WEB / "app.js").read_text(encoding="utf-8")
    assert "/static/app.js" in page and "/static/style.css" in page
    import re

    for element_id in set(re.findall(r'\$\("([\w-]+)"\)', script)):
        assert f'id="{element_id}"' in page, element_id


def test_dynamic_ids_used_by_the_script_exist_in_the_page():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "conditions-chips",
        "conditions-input",
        "allergies-chips",
        "allergies-input",
    ):
        assert f'id="{element_id}"' in page, element_id


def test_theme_toggle_and_early_theme_script_are_present():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    style = (WEB / "style.css").read_text(encoding="utf-8")
    assert 'id="theme-toggle"' in page
    assert page.index("data-theme") < page.index("/static/style.css")
    assert ':root[data-theme="dark"]' in style
