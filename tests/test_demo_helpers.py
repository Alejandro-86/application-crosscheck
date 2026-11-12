from crosscheck.compare import Flag
from crosscheck.demo_helpers import (
    flag_id,
    group_flags,
    highlight,
    load_demo_data,
    load_reviews,
    save_review,
)


def test_highlight_wraps_the_first_occurrence_only():
    out = highlight("Diagnoses: asthma.\nNo history of asthma.", ["asthma"])
    assert out.count("<mark>") == 1
    assert out.startswith("Diagnoses: <mark>asthma</mark>.")


def test_highlight_escapes_html_so_it_is_safe_to_render():
    out = highlight("<script>alert(1)</script> asthma", ["asthma"])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out and "<mark>asthma</mark>" in out
    assert "<b>" not in highlight("x", ["<b>"])


def test_highlight_ignores_quotes_that_are_not_present():
    assert highlight("plain text", ["missing"]) == "plain text"


def test_group_flags_orders_by_severity():
    flags = [
        Flag("unverified_declaration", "gout", "m"),
        Flag("undisclosed_allergy", "latex", "m"),
        Flag("source_conflict", "metformin", "m"),
    ]
    assert list(group_flags(flags)) == ["discrepancy", "data_quality", "unverified"]


def test_reviews_round_trip(tmp_path):
    path = tmp_path / "reviews.json"
    key = flag_id("A1", Flag("undisclosed_allergy", "latex", "m"))
    assert load_reviews(path) == {}
    save_review(path, key, "Confirm")
    assert load_reviews(path) == {key: "Confirm"}
    save_review(path, key, "—")
    assert load_reviews(path) == {}


def test_load_demo_data_generates_when_missing(tmp_path):
    data = load_demo_data(tmp_path / "gen")
    assert data.applications
    assert all(app.mrn in data.docs_by_mrn for app in data.applications)
