"""The audit log and reviewer decisions: a small writable SQLite file next to the read-only store.

Every check is recorded (what ran, with which model and prompt version, what it read, what it
flagged with its evidence, what it cost) and every reviewer decision is appended as an event, so
the question "why was this flagged, by what, and who decided what" can always be answered later.
Nothing here is ever updated in place except a run's status; decisions are append-only events and
the current decision is the latest event for a flag.
"""

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AUDIT_NAME = "audit.db"
DECISIONS = ("Confirm", "Dismiss", "—")
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    app_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    extractor TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    retrieval TEXT NOT NULL,
    top_k INTEGER,
    embedder TEXT,
    chars_sent INTEGER,
    chars_total INTEGER,
    n_flags INTEGER NOT NULL,
    dropped INTEGER NOT NULL,
    reasons TEXT NOT NULL,
    unmapped TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL,
    duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_app ON runs (app_id, ts);
CREATE TABLE IF NOT EXISTS run_flags (
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    flag_id TEXT NOT NULL,
    type TEXT NOT NULL,
    field TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_flags_run ON run_flags (run_id);
CREATE TABLE IF NOT EXISTS decision_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    flag_id TEXT NOT NULL,
    app_id TEXT,
    flag_type TEXT,
    field TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('Confirm', 'Dismiss', '—')),
    run_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_flag ON decision_events (flag_id, id);
"""


def prompt_version(*prompts: str) -> str:
    """A short hash of the prompt text, so a run records exactly which prompt produced it."""
    return hashlib.sha256("\n".join(prompts).encode("utf-8")).hexdigest()[:8]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class RunRecord:
    app_id: str
    extractor: str
    retrieval: str
    n_flags: int
    flags: list[dict] = field(default_factory=list)
    source: str = "check"
    status: str = "checked"
    model: str | None = None
    prompt_version: str | None = None
    top_k: int | None = None
    embedder: str | None = None
    chars_sent: int | None = None
    chars_total: int | None = None
    dropped: int = 0
    reasons: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0


class AuditDB:
    """A fresh connection per call keeps it safe to use from several threads."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def record_run(self, run: RunRecord) -> str:
        run_id = uuid.uuid4().hex[:12]
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO runs VALUES (" + ", ".join("?" * 21) + ")",
                (
                    run_id,
                    _now(),
                    run.app_id,
                    run.source,
                    run.status,
                    run.extractor,
                    run.model,
                    run.prompt_version,
                    run.retrieval,
                    run.top_k,
                    run.embedder,
                    run.chars_sent,
                    run.chars_total,
                    run.n_flags,
                    run.dropped,
                    json.dumps(run.reasons),
                    json.dumps(run.unmapped),
                    run.input_tokens,
                    run.output_tokens,
                    run.cost_usd,
                    run.duration_ms,
                ),
            )
            conn.executemany(
                "INSERT INTO run_flags VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        f["id"],
                        f["type"],
                        f["field"],
                        f["severity"],
                        f["message"],
                        json.dumps(f["evidence"]),
                    )
                    for f in run.flags
                ],
            )
            conn.commit()
        return run_id

    def set_status(self, run_id: str, status: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))
            conn.commit()

    _RUN_COLUMNS = (
        "run_id, ts, app_id, source, status, extractor, model, prompt_version, retrieval, top_k, "
        "embedder, chars_sent, chars_total, n_flags, dropped, reasons, unmapped, input_tokens, "
        "output_tokens, cost_usd, duration_ms"
    )

    def _run_dict(self, row: tuple) -> dict:
        keys = [c.strip() for c in self._RUN_COLUMNS.split(",")]
        data = dict(zip(keys, row, strict=True))
        data["reasons"] = json.loads(data["reasons"])
        data["unmapped"] = json.loads(data["unmapped"])
        return data

    def runs(self, app_id: str | None = None, limit: int = 50) -> list[dict]:
        query = f"SELECT {self._RUN_COLUMNS} FROM runs"
        params: list = []
        if app_id:
            query += " WHERE app_id = ?"
            params.append(app_id)
        query += " ORDER BY ts DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        with closing(self._connect()) as conn:
            return [self._run_dict(r) for r in conn.execute(query, params)]

    def run(self, run_id: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT {self._RUN_COLUMNS} FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            data = self._run_dict(row)
            data["flags"] = [
                {
                    "id": i,
                    "type": t,
                    "field": f,
                    "severity": s,
                    "message": m,
                    "evidence": json.loads(e),
                }
                for i, t, f, s, m, e in conn.execute(
                    "SELECT flag_id, type, field, severity, message, evidence FROM run_flags "
                    "WHERE run_id = ? ORDER BY rowid",
                    (run_id,),
                )
            ]
            return data

    def add_decision(self, flag_id: str, decision: str, run_id: str | None = None) -> None:
        if decision not in DECISIONS:
            raise ValueError(f"Invalid decision {decision!r}")
        parts = flag_id.split("|")
        app_id, flag_type, field_name = parts if len(parts) == 3 else (None, None, None)
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO decision_events (ts, flag_id, app_id, flag_type, field, decision, "
                "run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), flag_id, app_id, flag_type, field_name, decision, run_id),
            )
            conn.commit()

    _LATEST = (
        "SELECT flag_id, flag_type, decision FROM decision_events e WHERE id = "
        "(SELECT MAX(id) FROM decision_events WHERE flag_id = e.flag_id)"
    )

    def decisions(self) -> dict[str, str]:
        """The current decision for each flag (the latest event; cleared decisions are omitted)."""
        with closing(self._connect()) as conn:
            return {f: d for f, _, d in conn.execute(self._LATEST) if d != "—"}

    def decision_history(self, flag_id: str) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT ts, decision, run_id FROM decision_events WHERE flag_id = ? ORDER BY id",
                (flag_id,),
            )
            return [{"ts": t, "decision": d, "run_id": r} for t, d, r in rows]

    def decision_stats(self) -> dict[str, dict[str, int]]:
        """Confirm and Dismiss counts per flag type, from current decisions only."""
        stats: dict[str, dict[str, int]] = {}
        with closing(self._connect()) as conn:
            for _, flag_type, decision in conn.execute(self._LATEST):
                if decision == "—":
                    continue
                bucket = stats.setdefault(flag_type or "unknown", {"Confirm": 0, "Dismiss": 0})
                bucket[decision] += 1
        return dict(sorted(stats.items()))

    def import_legacy_reviews(self, path: Path) -> int:
        """One-off: bring decisions from the old reviews.json in, if the log is still empty."""
        if not path.exists():
            return 0
        with closing(self._connect()) as conn:
            if conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]:
                return 0
        imported = 0
        for flag_id, decision in json.loads(path.read_text(encoding="utf-8")).items():
            if decision in ("Confirm", "Dismiss"):
                self.add_decision(flag_id, decision, run_id="imported")
                imported += 1
        return imported


def audit_path(data_dir: Path) -> Path:
    return data_dir / AUDIT_NAME
