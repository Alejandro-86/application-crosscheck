"""Command line: python -m crosscheck.generate --n-docs 200 --seed 42 --out data/generated"""

import argparse
import json
from pathlib import Path

from crosscheck.applications import applications_to_dicts, generate_applications
from crosscheck.pharmacy import generate_pharmacy, pharmacy_to_dicts
from crosscheck.synthetic import generate_corpus, write_corpus


def _dump(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic corpus and sources.")
    parser.add_argument("--n-docs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args()

    patients, documents = generate_corpus(args.n_docs, args.seed)
    write_corpus(patients, documents, args.out)
    pharmacy = generate_pharmacy(patients, args.seed)
    applications = generate_applications(patients, documents, pharmacy, args.seed)
    _dump(args.out / "pharmacy.json", pharmacy_to_dicts(pharmacy))
    _dump(args.out / "applications.json", applications_to_dicts(applications))
    planted = sum(len(a.planted_errors) for a in applications)
    print(
        f"Wrote {len(documents)} notes, {len(patients)} patients, {len(pharmacy)} pharmacy rows "
        f"and {len(applications)} applications ({planted} planted errors) to {args.out}"
    )


if __name__ == "__main__":
    main()
