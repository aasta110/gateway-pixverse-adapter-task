.PHONY: setup starter-check verify release-check

setup:
	uv sync --locked

starter-check:
	uv run --frozen pytest -q tests/test_runner.py

verify:
	uv run --frozen pytest -q

release-check: starter-check
	git diff --check
