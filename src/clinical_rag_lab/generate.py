"""Command line: python -m clinical_rag_lab.generate --n-docs 200 --seed 42 --out data/generated"""

import argparse
from pathlib import Path

from clinical_rag_lab.questions import generate_questions, write_questions
from clinical_rag_lab.synthetic import generate_corpus, write_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic corpus and questions.")
    parser.add_argument("--n-docs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args()

    patients, documents = generate_corpus(args.n_docs, args.seed)
    write_corpus(patients, documents, args.out)
    questions = generate_questions(patients, documents, args.seed)
    write_questions(questions, args.out / "questions.json")
    print(
        f"Wrote {len(documents)} documents, {len(patients)} patients and "
        f"{len(questions)} questions to {args.out}"
    )


if __name__ == "__main__":
    main()
