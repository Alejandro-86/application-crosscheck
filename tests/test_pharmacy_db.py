import json
import sqlite3
from dataclasses import asdict

import pytest

from crosscheck.corpus import load_pharmacy
from crosscheck.generate import generate_all
from crosscheck.pharmacy import PharmacyRow
from crosscheck.pharmacy_db import PharmacyDB, create_db, db_path, ensure_db

ROWS = [
    PharmacyRow("111111", "metformin", 500, "mg", "2025-03-01"),
    PharmacyRow("111111", "atorvastatin", 20, "mg", "2025-02-02"),
    PharmacyRow("222222", "sertraline", 50, "mg", "2025-04-04"),
]


@pytest.fixture()
def db(tmp_path):
    create_db(tmp_path / "pharmacy.db", ROWS)
    return PharmacyDB(tmp_path / "pharmacy.db")


def test_rows_for_mrn_returns_only_that_applicant_in_a_stable_order(db):
    rows = db.rows_for_mrn("111111")
    assert [r.drug for r in rows] == ["atorvastatin", "metformin"]
    assert db.rows_for_mrn("999999") == []


def test_all_rows_round_trip_and_counts(db):
    assert [asdict(r) for r in db.all_rows()] == [asdict(r) for r in ROWS]
    assert db.counts() == {"rows": 3, "patients": 2}


def test_queries_are_parameterised(db):
    assert db.rows_for_mrn("111111' OR '1'='1") == []
    assert db.rows_for_mrn("1'; DROP TABLE dispensing; --") == []
    assert db.counts()["rows"] == 3


def test_the_app_connection_is_read_only(db):
    with db._connect() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM dispensing")
    with db._connect() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute(
            "INSERT INTO dispensing (mrn, drug, dose, last_dispensed) VALUES ('1','x',1,'d')"
        )
    assert db.counts()["rows"] == 3


def test_schema_rejects_a_non_positive_dose(tmp_path):
    with pytest.raises(sqlite3.IntegrityError):
        create_db(tmp_path / "bad.db", [PharmacyRow("1", "metformin", 0, "mg", "2025-01-01")])


def test_missing_database_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        PharmacyDB(tmp_path / "nope.db")


def test_create_db_replaces_an_existing_file(tmp_path):
    path = tmp_path / "pharmacy.db"
    create_db(path, ROWS)
    create_db(path, ROWS[:1])
    assert PharmacyDB(path).counts() == {"rows": 1, "patients": 1}


def test_an_older_pharmacy_json_is_migrated_into_a_database(tmp_path):
    (tmp_path / "pharmacy.json").write_text(json.dumps([asdict(r) for r in ROWS]), encoding="utf-8")
    assert not db_path(tmp_path).exists()
    ensure_db(tmp_path)
    assert PharmacyDB(db_path(tmp_path)).counts() == {"rows": 3, "patients": 2}
    assert len(load_pharmacy(tmp_path)) == 3


def test_generate_all_builds_a_database_with_a_row_for_most_medications(tmp_path):
    generate_all(tmp_path, n_docs=60, seed=9)
    assert db_path(tmp_path).exists()
    assert not (tmp_path / "pharmacy.json").exists()
    counts = PharmacyDB(db_path(tmp_path)).counts()
    assert counts["rows"] > 20 and counts["patients"] > 10
