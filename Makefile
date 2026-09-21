.PHONY: test venv

venv:
	python3 -m venv .venv && .venv/bin/pip install pytest

test:
	.venv/bin/python -m pytest -q
