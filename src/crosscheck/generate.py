"""Command line: python -m crosscheck.generate --n-docs 200 --seed 42 --out data/generated"""

import argparse
import json
import random
import shutil
from pathlib import Path

from crosscheck.applications import applications_to_dicts, generate_applications
from crosscheck.forms import render_form
from crosscheck.pharmacy import generate_pharmacy
from crosscheck.pharmacy_db import create_db, db_path
from crosscheck.synthetic import generate_corpus, write_corpus


def _dump(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_forms(applications, patients, out: Path, seed: int) -> None:
    """Write every application as a raw text form, replacing any forms from an earlier run."""
    forms_dir = out / "applications"
    shutil.rmtree(forms_dir, ignore_errors=True)
    forms_dir.mkdir(parents=True)
    rng = random.Random(seed + 3)
    by_id = {p.patient_id: p for p in patients}
    for app in applications:
        text = render_form(app, by_id[app.patient_id], rng)
        (forms_dir / f"{app.app_id}.txt").write_text(text, encoding="utf-8")


def generate_all(out: Path, n_docs: int = 200, seed: int = 42, hard_fraction: float = 0.0) -> str:
    patients, documents = generate_corpus(n_docs, seed, hard_fraction)
    write_corpus(patients, documents, out)
    pharmacy = generate_pharmacy(patients, seed)
    applications = generate_applications(patients, documents, pharmacy, seed)
    create_db(db_path(out), pharmacy)
    _dump(out / "applications.json", applications_to_dicts(applications))
    write_forms(applications, patients, out, seed)
    planted = sum(len(a.planted_errors) for a in applications)
    return (
        f"Wrote {len(documents)} notes, {len(patients)} patients, {len(applications)} applications "
        f"({planted} planted errors), raw application forms and a pharmacy database with "
        f"{len(pharmacy)} rows to {out}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic corpus and sources.")
    parser.add_argument("--n-docs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--hard-fraction",
        type=float,
        default=0.0,
        help="share of notes rewritten as narrative letters with traps (0 to 1)",
    )
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args()
    print(generate_all(args.out, args.n_docs, args.seed, args.hard_fraction))


if __name__ == "__main__":
    main()
