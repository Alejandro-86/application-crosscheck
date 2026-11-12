"""Command line: python -m crosscheck.generate --n-docs 200 --seed 42 --out data/generated"""

import argparse
import json
from pathlib import Path

from crosscheck.applications import applications_to_dicts, generate_applications
from crosscheck.pharmacy import generate_pharmacy
from crosscheck.pharmacy_db import create_db, db_path
from crosscheck.synthetic import generate_corpus, write_corpus


def _dump(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def generate_all(out: Path, n_docs: int = 200, seed: int = 42) -> str:
    patients, documents = generate_corpus(n_docs, seed)
    write_corpus(patients, documents, out)
    pharmacy = generate_pharmacy(patients, seed)
    applications = generate_applications(patients, documents, pharmacy, seed)
    create_db(db_path(out), pharmacy)
    _dump(out / "applications.json", applications_to_dicts(applications))
    planted = sum(len(a.planted_errors) for a in applications)
    return (
        f"Wrote {len(documents)} notes, {len(patients)} patients, {len(applications)} applications "
        f"({planted} planted errors) and a pharmacy database with {len(pharmacy)} rows to {out}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic corpus and sources.")
    parser.add_argument("--n-docs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args()
    print(generate_all(args.out, args.n_docs, args.seed))


if __name__ == "__main__":
    main()
