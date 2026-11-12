"""The application store: the SQLite database the ingestion step fills and the app reads.

It holds the raw text of every application form and clinical note, the declared items parsed from
each form (with the canonical term when one is known), the note chunks with their embeddings, and
a log of everything the parser could not make sense of. The app opens it read-only and only runs
parameterised queries. Only `crosscheck.ingest` writes to it.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from crosscheck.applications import Application, DeclaredMedication, application_from_dict
from crosscheck.embeddings import unpack
from crosscheck.retrieval import Chunk, Index

STORE_NAME = "store.db"
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (
    path TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('application', 'note')),
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'failed'))
);
CREATE TABLE applications (
    app_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL REFERENCES sources (path) ON DELETE CASCADE,
    name TEXT NOT NULL,
    mrn TEXT NOT NULL,
    age INTEGER,
    sex TEXT,
    received TEXT,
    raw_text TEXT NOT NULL
);
CREATE INDEX idx_applications_mrn ON applications (mrn);
CREATE TABLE declared_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL REFERENCES applications (app_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('condition', 'medication', 'allergy')),
    raw TEXT NOT NULL,
    canonical TEXT,
    dose INTEGER CHECK (dose IS NULL OR dose > 0),
    unit TEXT
);
CREATE INDEX idx_declared_app ON declared_items (app_id);
CREATE TABLE notes (
    doc_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL REFERENCES sources (path) ON DELETE CASCADE,
    mrn TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX idx_notes_mrn ON notes (mrn);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES notes (doc_id) ON DELETE CASCADE,
    mrn TEXT NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX idx_chunks_mrn ON chunks (mrn);
CREATE TABLE issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL REFERENCES sources (path) ON DELETE CASCADE,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    message TEXT NOT NULL
);
"""


@dataclass
class DeclaredItem:
    kind: str
    raw: str
    canonical: str | None
    dose: int | None = None
    unit: str | None = None


@dataclass
class StoredApplication:
    app_id: str
    name: str
    mrn: str
    age: int | None
    sex: str | None
    received: str | None
    raw_text: str
    items: list[DeclaredItem] = field(default_factory=list)

    def to_application(self) -> Application:
        """The checkable application: only items that mapped to a canonical term."""
        conditions: list[str] = []
        allergies: list[str] = []
        medications: dict[str, DeclaredMedication] = {}
        for item in self.items:
            if item.canonical is None:
                continue
            if item.kind == "condition" and item.canonical not in conditions:
                conditions.append(item.canonical)
            elif item.kind == "allergy" and item.canonical not in allergies:
                allergies.append(item.canonical)
            elif item.kind == "medication" and item.dose is not None:
                medications[item.canonical] = DeclaredMedication(
                    item.canonical, item.dose, item.unit or "mg"
                )
        return Application(
            app_id=self.app_id,
            patient_id="",
            name=self.name,
            mrn=self.mrn,
            declared_conditions=conditions,
            declared_medications=list(medications.values()),
            declared_allergies=allergies,
        )


@dataclass
class StoredNote:
    doc_id: str
    mrn: str
    text: str


def store_path(data_dir: Path) -> Path:
    return data_dir / STORE_NAME


class StoreDB:
    """Read-only access. A fresh connection per call keeps it safe to use from several threads."""

    def __init__(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"Application store not found: {path}")
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)

    def meta(self) -> dict[str, str]:
        with closing(self._connect()) as conn:
            return dict(conn.execute("SELECT key, value FROM meta"))

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            return {
                "applications": conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
                "notes": conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
                "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "issues": conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0],
                "errors": conn.execute(
                    "SELECT COUNT(*) FROM issues WHERE severity = 'error'"
                ).fetchone()[0],
            }

    def applications(self) -> list[StoredApplication]:
        with closing(self._connect()) as conn:
            apps = [
                StoredApplication(*row)
                for row in conn.execute(
                    "SELECT app_id, name, mrn, age, sex, received, raw_text FROM applications "
                    "ORDER BY app_id"
                )
            ]
            by_id = {a.app_id: a for a in apps}
            for app_id, kind, raw, canonical, dose, unit in conn.execute(
                "SELECT app_id, kind, raw, canonical, dose, unit FROM declared_items ORDER BY id"
            ):
                by_id[app_id].items.append(DeclaredItem(kind, raw, canonical, dose, unit))
        return apps

    def application(self, app_id: str) -> StoredApplication | None:
        return next((a for a in self.applications() if a.app_id == app_id), None)

    def notes_for_mrn(self, mrn: str) -> list[StoredNote]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT doc_id, mrn, text FROM notes WHERE mrn = ? ORDER BY doc_id", (mrn,)
            )
            return [StoredNote(*row) for row in rows]

    def all_notes(self) -> list[StoredNote]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT doc_id, mrn, text FROM notes ORDER BY doc_id")
            return [StoredNote(*row) for row in rows]

    def index_for_mrn(self, mrn: str) -> Index:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT doc_id, idx, text, embedding FROM chunks WHERE mrn = ? "
                "ORDER BY doc_id, idx",
                (mrn,),
            )
            return [(Chunk(doc_id, idx, text), unpack(blob)) for doc_id, idx, text, blob in rows]

    def issues(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT source_path, severity, message FROM issues ORDER BY id")
            return [{"source": s, "severity": v, "message": m} for s, v, m in rows]


def load_parsed_applications(data_dir: Path) -> list[Application]:
    """Applications as parsed from their forms, with the answer key joined from the truth file.

    The truth file (applications.json) is only used for planted errors and the patient link; the
    declared items come from the store, so the whole path (form -> parser -> database) is tested.
    """
    truth: dict[str, Application] = {}
    truth_file = data_dir / "applications.json"
    if truth_file.exists():
        truth = {
            a.app_id: a
            for a in (application_from_dict(d) for d in json.loads(truth_file.read_text()))
        }
    apps = []
    for stored in StoreDB(store_path(data_dir)).applications():
        app = stored.to_application()
        if app.app_id in truth:
            app.planted_errors = truth[app.app_id].planted_errors
            app.patient_id = truth[app.app_id].patient_id
        apps.append(app)
    return apps
