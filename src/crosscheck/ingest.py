"""The scraper: read raw application forms and clinical notes, parse them, load the store.

    python -m crosscheck.ingest --data data/generated --embedder hashing

Idempotent: a file whose content hash has not changed is skipped, a changed file is replaced, and
a file that disappeared is removed. Nothing is guessed: a form missing a required field is not
loaded and the reason is logged, and a term with no canonical mapping is stored as written, with a
warning, rather than being forced onto the closest match. The whole run is one transaction, so a
failure (for example a spend cap hit while embedding) leaves the store as it was.
"""

import argparse
import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from crosscheck.embeddings import Embedder, get_embedder, pack
from crosscheck.pipeline import MRN_PATTERN
from crosscheck.retrieval import chunk_note
from crosscheck.store import SCHEMA, DeclaredItem, store_path
from crosscheck.terminology import normalise_allergy, normalise_condition, normalise_drug

PARSER_VERSION = "1"
NONE_MARKERS = {
    "none",
    "nil",
    "n/a",
    "na",
    "no",
    "nkda",
    "none known",
    "no known allergies",
    "no known drug allergies",
    "no regular medication",
    "no regular medications",
}
SECTION_HEADER = re.compile(r"^\s*2(?P<letter>[abc])\.[^:]*:(?P<rest>.*)$")
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
MEDICATION_ITEM = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z-]*)\s*(?P<dose>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]+)\b"
)


class FormError(ValueError):
    """The form cannot be loaded; the message says why."""


@dataclass
class ParsedApplication:
    app_id: str
    name: str
    mrn: str
    age: int | None
    sex: str | None
    received: str | None
    items: list[DeclaredItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _field(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _blocks(text: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        header = SECTION_HEADER.match(line)
        if header:
            current = header["letter"]
            blocks[current] = []
            if header["rest"].strip():
                blocks[current].append(header["rest"].strip())
        elif not line.strip() or line.startswith("SECTION"):
            current = None
        elif current:
            blocks[current].append(BULLET.sub("", line).strip())
    return blocks


def _entries(lines: list[str], separators: str) -> list[str]:
    entries = []
    for line in lines:
        for part in re.split(separators, BULLET.sub("", line)):
            part = part.strip().strip(".")
            if part and part.lower() not in NONE_MARKERS:
                entries.append(part)
    return entries


def _medication(raw: str, warnings: list[str]) -> DeclaredItem:
    match = MEDICATION_ITEM.match(raw)
    if not match:
        warnings.append(f"medication '{raw}': could not read a name and dose")
        return DeclaredItem("medication", raw, None)
    drug = normalise_drug(match["name"])
    unit = match["unit"].lower()
    dose = float(match["dose"])
    if drug is None:
        warnings.append(f"medication '{match['name']}': no canonical mapping")
    elif unit != "mg" or dose != int(dose) or dose <= 0:
        warnings.append(f"medication '{raw}': only whole-number mg doses are supported")
        drug = None
    return DeclaredItem(
        "medication", raw, drug, int(dose) if drug else None, "mg" if drug else None
    )


def parse_application(text: str) -> ParsedApplication:
    app_id = _field(text, r"^Reference:\s*(\S+)")
    name = _field(text, r"^Full name:\s*(.+)$")
    mrn = _field(text, r"^Patient ID \(MRN\):\s*(\d+)")
    missing = [
        label
        for label, value in (("Reference", app_id), ("Full name", name), ("Patient ID (MRN)", mrn))
        if not value
    ]
    blocks = _blocks(text)
    missing += [f"section 2{letter}" for letter in "abc" if letter not in blocks]
    if missing:
        raise FormError("missing " + ", ".join(missing))
    assert app_id and name and mrn

    age = _field(text, r"^Age:\s*(\d{1,3})\b")
    sex = (_field(text, r"Sex:\s*([A-Za-z]+)") or "")[:1].upper() or None
    parsed = ParsedApplication(
        app_id=app_id,
        name=name,
        mrn=mrn,
        age=int(age) if age else None,
        sex=sex if sex in ("F", "M") else None,
        received=_field(text, r"^Date received:\s*(\d{4}-\d{2}-\d{2})"),
    )
    for raw in _entries(blocks["a"], r"[;,]"):
        canonical = normalise_condition(raw)
        if canonical is None:
            parsed.warnings.append(f"condition '{raw}': no canonical mapping")
        parsed.items.append(DeclaredItem("condition", raw, canonical))
    for raw in _entries(blocks["b"], r";"):
        parsed.items.append(_medication(raw, parsed.warnings))
    for raw in _entries(blocks["c"], r"[;,]"):
        parsed.items.append(DeclaredItem("allergy", raw, normalise_allergy(raw)))
    return parsed


@dataclass
class IngestReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    warnings: int = 0
    chunks_embedded: int = 0
    embedder: str = ""

    def summary(self) -> str:
        return (
            f"Ingested with {self.embedder}: {self.added} added, {self.updated} updated, "
            f"{self.unchanged} unchanged, {self.removed} removed, {self.failed} failed; "
            f"{self.warnings} parser warnings; {self.chunks_embedded} chunks embedded."
        )


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    if fresh:
        conn.executescript(SCHEMA)
    return conn


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def _scan(data_dir: Path) -> dict[str, tuple[str, str, str]]:
    """Source key -> (kind, text, sha256) for every raw file on disk."""
    found = {}
    for kind, folder in (("application", "applications"), ("note", "docs")):
        for path in sorted((data_dir / folder).glob("*.txt")):
            raw = path.read_bytes()
            found[f"{folder}/{path.name}"] = (
                kind,
                raw.decode("utf-8", errors="replace"),
                hashlib.sha256(raw).hexdigest(),
            )
    return found


def _fail(conn: sqlite3.Connection, key: str, kind: str, sha: str, message: str) -> None:
    conn.execute(
        "INSERT INTO sources (path, kind, sha256, status) VALUES (?, ?, ?, 'failed')",
        (key, kind, sha),
    )
    conn.execute(
        "INSERT INTO issues (source_path, severity, message) VALUES (?, 'error', ?)",
        (key, message),
    )


def _load_application(conn, key: str, sha: str, text: str, report: IngestReport) -> None:
    try:
        parsed = parse_application(text)
        taken = conn.execute(
            "SELECT source_path FROM applications WHERE app_id = ?", (parsed.app_id,)
        ).fetchone()
        if taken:
            raise FormError(f"duplicate reference {parsed.app_id} (already loaded from {taken[0]})")
    except FormError as error:
        _fail(conn, key, "application", sha, str(error))
        report.failed += 1
        return
    conn.execute(
        "INSERT INTO sources (path, kind, sha256, status) VALUES (?, 'application', ?, 'ok')",
        (key, sha),
    )
    conn.execute(
        "INSERT INTO applications (app_id, source_path, name, mrn, age, sex, received, raw_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            parsed.app_id,
            key,
            parsed.name,
            parsed.mrn,
            parsed.age,
            parsed.sex,
            parsed.received,
            text,
        ),
    )
    conn.executemany(
        "INSERT INTO declared_items (app_id, kind, raw, canonical, dose, unit) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(parsed.app_id, i.kind, i.raw, i.canonical, i.dose, i.unit) for i in parsed.items],
    )
    conn.executemany(
        "INSERT INTO issues (source_path, severity, message) VALUES (?, 'warning', ?)",
        [(key, w) for w in parsed.warnings],
    )
    report.warnings += len(parsed.warnings)


def _load_note(conn, key: str, sha: str, text: str, report: IngestReport) -> None:
    match = MRN_PATTERN.search(text)
    if not match:
        _fail(conn, key, "note", sha, "no MRN found in the note")
        report.failed += 1
        return
    doc_id = Path(key).stem
    conn.execute(
        "INSERT INTO sources (path, kind, sha256, status) VALUES (?, 'note', ?, 'ok')", (key, sha)
    )
    conn.execute(
        "INSERT INTO notes (doc_id, source_path, mrn, text) VALUES (?, ?, ?, ?)",
        (doc_id, key, match[1], text),
    )


def ingest(data_dir: Path, embedder: Embedder, store_file: Path | None = None) -> IngestReport:
    report = IngestReport(embedder=embedder.name)
    with closing(_connect(store_file or store_path(data_dir))) as conn:
        try:
            _run(conn, data_dir, embedder, report)
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
    return report


def _run(conn, data_dir: Path, embedder: Embedder, report: IngestReport) -> None:
    reparse_all = _get_meta(conn, "parser_version") != PARSER_VERSION
    reembed_all = _get_meta(conn, "embedder") != embedder.name
    known = dict(conn.execute("SELECT path, sha256 FROM sources"))
    on_disk = _scan(data_dir)

    for key in sorted(set(known) - set(on_disk)):
        conn.execute("DELETE FROM sources WHERE path = ?", (key,))
        report.removed += 1

    for key, (kind, text, sha) in on_disk.items():
        if key in known and known[key] == sha and not reparse_all:
            report.unchanged += 1
            continue
        if key in known:
            conn.execute("DELETE FROM sources WHERE path = ?", (key,))
            report.updated += 1
        else:
            report.added += 1
        if kind == "application":
            _load_application(conn, key, sha, text, report)
        else:
            _load_note(conn, key, sha, text, report)

    if reembed_all:
        conn.execute("DELETE FROM chunks")
    needing = [
        row
        for row in conn.execute(
            "SELECT n.doc_id, n.mrn, n.text FROM notes n "
            "WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = n.doc_id) ORDER BY n.doc_id"
        )
    ]
    pending = [
        (doc_id, mrn, chunk) for doc_id, mrn, text in needing for chunk in chunk_note(doc_id, text)
    ]
    vectors = embedder.embed([chunk.text for _, _, chunk in pending]) if pending else []
    conn.executemany(
        "INSERT INTO chunks (doc_id, mrn, idx, text, embedding) VALUES (?, ?, ?, ?, ?)",
        [
            (doc_id, mrn, chunk.idx, chunk.text, pack(vector))
            for (doc_id, mrn, chunk), vector in zip(pending, vectors, strict=True)
        ],
    )
    report.chunks_embedded = len(pending)
    _set_meta(conn, "parser_version", PARSER_VERSION)
    _set_meta(conn, "embedder", embedder.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load raw forms and notes into the store.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--embedder", choices=["hashing", "openai"], default="hashing")
    args = parser.parse_args()
    settings = None
    if args.embedder == "openai":
        from crosscheck.config import get_settings

        settings = get_settings()
    print(ingest(args.data, get_embedder(args.embedder, settings)).summary())


if __name__ == "__main__":
    main()
