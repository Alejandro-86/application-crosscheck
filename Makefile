.PHONY: install lint format test data

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
