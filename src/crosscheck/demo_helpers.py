"""UI-independent helpers for the demo, kept separate so they can be tested without a browser."""

import html
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from crosscheck.applications import Application
from crosscheck.compare import Flag
from crosscheck.corpus import (
    LoadedDocument,
    load_applications,
    load_documents,
    load_pharmacy,
)
from crosscheck.generate import generate_all
from crosscheck.pharmacy import PharmacyRow
from crosscheck.pipeline import index_documents

SEVERITY_ORDER = ["discrepancy", "data_quality", "unverified"]
SEVERITY_LABEL = {
    "discrepancy": "Discrepancies (evidence in the sources)",
    "data_quality": "Source conflicts (the sources disagree with each other)",
    "unverified": "Unverified (nothing found; absence is not proof)",
}


@dataclass
class DemoData:
    applications: list[Application]
    docs_by_mrn: dict[str, list[LoadedDocument]]
    pharmacy_by_mrn: dict[str, list[PharmacyRow]]


def load_demo_data(data_dir: Path) -> DemoData:
    if not (data_dir / "applications.json").exists():
        generate_all(data_dir)
    pharmacy: dict[str, list[PharmacyRow]] = defaultdict(list)
    for row in load_pharmacy(data_dir):
        pharmacy[row.mrn].append(row)
    return DemoData(
        applications=load_applications(data_dir),
        docs_by_mrn=index_documents(load_documents(data_dir)),
        pharmacy_by_mrn=dict(pharmacy),
    )


def highlight(text: str, quotes: list[str]) -> str:
    """HTML-escape text, then wrap each quote's first occurrence in <mark>. Safe to render."""
    safe = html.escape(text)
    for quote in quotes:
        target = html.escape(quote)
        if target and target in safe and "<mark>" + target not in safe:
            safe = safe.replace(target, f"<mark>{target}</mark>", 1)
    return safe


def group_flags(flags: list[Flag]) -> dict[str, list[Flag]]:
    grouped: dict[str, list[Flag]] = {s: [] for s in SEVERITY_ORDER}
    for flag in flags:
        grouped[flag.severity].append(flag)
    return {s: items for s, items in grouped.items() if items}


def flag_id(app_id: str, flag: Flag) -> str:
    return f"{app_id}|{flag.type}|{flag.field}"


def load_reviews(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_review(path: Path, key: str, decision: str) -> None:
    reviews = load_reviews(path)
    if decision == "—":
        reviews.pop(key, None)
    else:
        reviews[key] = decision
    path.write_text(json.dumps(reviews, indent=2, sort_keys=True) + "\n", encoding="utf-8")
