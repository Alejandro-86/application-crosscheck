"""Command line: python -m crosscheck.generate --n-docs 200 --seed 42 --out data/generated"""

import argparse
from pathlib import Path

from crosscheck.synthetic import generate_corpus, write_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic corpus.")
    parser.add_argument("--n-docs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args()

    patients, documents = generate_corpus(args.n_docs, args.seed)
    write_corpus(patients, documents, args.out)
    print(f"Wrote {len(documents)} documents and {len(patients)} patients to {args.out}")


if __name__ == "__main__":
    main()
