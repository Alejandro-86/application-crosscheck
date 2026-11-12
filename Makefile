.PHONY: install lint format test data eval-rules eval-llm demo

install:
	pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest

data:
	python -m crosscheck.generate --n-docs 200 --seed 42 --out data/generated

eval-rules:
	python -m crosscheck.evaluate --extractor rules --out docs/results-rules.md

eval-llm:
	python -m crosscheck.evaluate --extractor llm --limit 20 --out docs/results-llm.md

demo:
	streamlit run src/crosscheck/demo.py
