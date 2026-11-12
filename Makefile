.PHONY: install lint format test data ingest eval-rules eval-rag eval-llm demo web

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
	python -m crosscheck.generate --n-docs 1000 --seed 42 --hard-fraction 0.4 --out data/generated
	python -m crosscheck.ingest --data data/generated

ingest:
	python -m crosscheck.ingest --data data/generated

eval-rules:
	python -m crosscheck.evaluate --extractor rules --out docs/results-rules.md

eval-rag:
	python -m crosscheck.evaluate --sweep --out docs/results-rag.md

eval-llm:
	python -m crosscheck.evaluate --extractor llm --limit 20 --out docs/results-llm.md

demo:
	streamlit run src/crosscheck/demo.py

web:
	uvicorn --factory crosscheck.api:create_app --port 8000
