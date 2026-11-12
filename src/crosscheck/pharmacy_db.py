"""The pharmacy dispensing database: a small SQLite file that the app reads from, read-only.

The generator creates and fills it (synthetic data). Application code only ever opens it in
read-only mode and only runs parameterised queries, so a request cannot change or leak more than
the rows it asks for.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from crosscheck.pharmacy import PharmacyRow

DB_NAME = "pharmacy.db"
SCHEMA = """
CREATE TABLE dispensing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mrn TEXT NOT NULL,
    drug TEXT NOT NULL,
    dose INTEGER NOT NULL CHECK (dose > 0),
    unit TEXT NOT NULL DEFAULT 'mg',
    last_dispensed TEXT NOT NULL
);
CREATE INDEX idx_dispensing_mrn ON dispensing (mrn);
"""


def db_path(data_dir: Path) -> Path:
    return data_dir / DB_NAME


def create_db(path: Path, rows: list[PharmacyRow]) -> None:
    """Create (or replace) the database file and fill it with the given rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO dispensing (mrn, drug, dose, unit, last_dispensed) VALUES (?, ?, ?, ?, ?)",
            [(r.mrn, r.drug, r.dose, r.unit, r.last_dispensed) for r in rows],
        )
        conn.commit()


def ensure_db(data_dir: Path) -> Path:
    """Return the database path, building it from an older pharmacy.json if only that exists."""
    path = db_path(data_dir)
    legacy = data_dir / "pharmacy.json"
    if not path.exists() and legacy.exists():
        raw = json.loads(legacy.read_text(encoding="utf-8"))
        create_db(path, [PharmacyRow(**row) for row in raw])
    return path


class PharmacyDB:
    """Read-only access. A fresh connection per call keeps it safe to use from several threads."""

    def __init__(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"Pharmacy database not found: {path}")
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)

    @staticmethod
    def _rows(cursor: sqlite3.Cursor) -> list[PharmacyRow]:
        return [PharmacyRow(mrn, drug, dose, unit, when) for mrn, drug, dose, unit, when in cursor]

    def rows_for_mrn(self, mrn: str) -> list[PharmacyRow]:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "SELECT mrn, drug, dose, unit, last_dispensed FROM dispensing "
                "WHERE mrn = ? ORDER BY drug, id",
                (mrn,),
            )
            return self._rows(cursor)

    def all_rows(self) -> list[PharmacyRow]:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "SELECT mrn, drug, dose, unit, last_dispensed FROM dispensing ORDER BY id"
            )
            return self._rows(cursor)

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            rows, patients = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT mrn) FROM dispensing"
            ).fetchone()
        return {"rows": rows, "patients": patients}
