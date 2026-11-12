"""Streamlit demo: cross-check one synthetic application against its sources.

Run with:  streamlit run src/crosscheck/demo.py     (after: pip install -e ".[demo]")
"""

from pathlib import Path

import streamlit as st

from crosscheck.config import ConfigError, get_settings
from crosscheck.demo_helpers import (
    SEVERITY_LABEL,
    flag_id,
    group_flags,
    highlight,
    load_demo_data,
    load_reviews,
    save_review,
)
from crosscheck.pipeline import check_application
from crosscheck.rules_extractor import RuleExtractor

DATA_DIR = Path("data/generated")
REVIEWS = DATA_DIR / "reviews.json"

st.set_page_config(page_title="Application cross-check", layout="wide")
st.title("Application cross-check")
st.caption(
    "Synthetic data only. Flags are data-quality signals for a human reviewer, not underwriting "
    "decisions. Every discrepancy cites its evidence; if nothing is found it is shown as "
    "unverified, never as a contradiction."
)


@st.cache_data
def get_data(path: str):
    return load_demo_data(Path(path))


data = get_data(str(DATA_DIR))

with st.sidebar:
    only_flawed = st.checkbox("Only applications with planted errors", value=True)
    apps = [a for a in data.applications if a.planted_errors or not only_flawed]
    app = st.selectbox(
        "Application",
        apps,
        format_func=lambda a: f"{a.app_id} - {a.name} ({len(a.planted_errors)} planted)",
    )
    mode = st.radio("Extractor", ["Rules (free baseline)", "LLM (uses your API key)"])

docs = data.docs_by_mrn.get(app.mrn, [])
rows = data.pharmacy_by_mrn.get(app.mrn, [])

result = None
if mode.startswith("Rules"):
    result = check_application(app, docs, rows, RuleExtractor())
else:
    state_key = f"llm-{app.app_id}"
    if st.sidebar.button("Run LLM extraction for this application"):
        try:
            from crosscheck.llm import ChatClient
            from crosscheck.llm_extractor import LLMExtractor

            st.session_state[state_key] = check_application(
                app, docs, rows, LLMExtractor(ChatClient(get_settings()))
            )
        except ConfigError as error:
            st.sidebar.error(str(error))
        except Exception as error:  # surface API or budget errors instead of crashing the page
            st.sidebar.error(f"{type(error).__name__}: {error}")
    result = st.session_state.get(state_key)
    if result is None:
        st.info("Press the button in the sidebar to run the model on this application's notes.")
        st.stop()

left, right = st.columns([1, 2])

with left:
    st.subheader(f"Application {app.app_id}")
    st.write(f"**{app.name}** (MRN {app.mrn})")
    st.markdown("**Declared conditions**")
    st.write(", ".join(app.declared_conditions) or "none")
    st.markdown("**Declared medications**")
    st.write(", ".join(f"{m.name} {m.dose} {m.unit}" for m in app.declared_medications) or "none")
    st.markdown("**Declared allergies**")
    st.write(", ".join(app.declared_allergies) or "none")
    with st.expander("Reveal the answer key (planted errors)"):
        if app.planted_errors:
            for error in app.planted_errors:
                st.write(f"- {error.type}: {error.field} ({error.detail})")
        else:
            st.write("This application is clean: no errors were planted.")

with right:
    st.subheader("Flags")
    if not result.flags:
        st.success("No flags: the application matches the sources.")
    reviews = load_reviews(REVIEWS)
    for severity, flags in group_flags(result.flags).items():
        st.markdown(f"**{SEVERITY_LABEL[severity]}**")
        for flag in flags:
            key = flag_id(app.app_id, flag)
            with st.container(border=True):
                st.write(f"`{flag.type}` - **{flag.field}**")
                st.write(flag.message)
                for evidence in flag.evidence:
                    where = evidence.doc_id or "pharmacy database"
                    st.caption(f"{evidence.source} / {where}: “{evidence.quote}”")
                if flag.severity == "unverified":
                    st.caption(f"Checked notes: {', '.join(flag.checked_docs) or 'none'}")
                options = ["—", "Confirm", "Dismiss"]
                choice = st.radio(
                    "Reviewer decision",
                    options,
                    index=options.index(reviews.get(key, "—")),
                    horizontal=True,
                    key=key,
                )
                if choice != reviews.get(key, "—"):
                    save_review(REVIEWS, key, choice)
    if result.dropped:
        st.warning(
            f"{len(result.dropped)} flag(s) were dropped: their evidence could not be verified."
        )

st.divider()
st.subheader("Sources")
quotes_by_doc: dict[str, list[str]] = {}
for flag in result.flags:
    for evidence in flag.evidence:
        if evidence.doc_id:
            quotes_by_doc.setdefault(evidence.doc_id, []).append(evidence.quote)

for doc in docs:
    with st.expander(
        f"Clinical note {doc.doc_id} ({doc.doc_type})", expanded=bool(quotes_by_doc.get(doc.doc_id))
    ):
        body = highlight(doc.text, quotes_by_doc.get(doc.doc_id, [])).replace("\n", "<br>")
        st.markdown(f"<div style='font-family: monospace'>{body}</div>", unsafe_allow_html=True)

with st.expander("Pharmacy database rows", expanded=bool(rows)):
    pharmacy_quotes = {e.quote for f in result.flags for e in f.evidence if e.source == "pharmacy"}
    for row in rows:
        line = row.citation()
        st.markdown(f"**{line}**" if line in pharmacy_quotes else line)
    if not rows:
        st.write("No pharmacy records for this patient.")
