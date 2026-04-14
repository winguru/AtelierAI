set shell := ["bash", "-lc"]

default:
	just --list

help:
	just --list

run:
	./start.sh

bootstrap:
	pip install -e app/src

bootstrap-local:
	python3 -m venv .venv
	. .venv/bin/activate && python -m pip install --upgrade pip && python -m pip install -r app/requirements.txt && python -m pip install -e app/src

smoke:
	bash -n start.sh app/entrypoint.sh
	./start.sh --help >/dev/null
	docker compose --profile dev config >/dev/null
	docker compose --profile runtime config >/dev/null

lint:
	if [ -x .venv/bin/ruff ]; then .venv/bin/ruff check --ignore E402 app/backend app/src; elif command -v ruff >/dev/null 2>&1; then ruff check --ignore E402 app/backend app/src; else echo "ruff not installed; skipping"; fi
	if [ -x .venv/bin/mypy ]; then .venv/bin/mypy app/src || true; elif command -v mypy >/dev/null 2>&1; then mypy app/src || true; else echo "mypy not installed; skipping"; fi

typecheck-civitai:
	if [ -x .venv/bin/mypy ]; then .venv/bin/mypy --check-untyped-defs app/src/atelierai/civitai; elif command -v mypy >/dev/null 2>&1; then mypy --check-untyped-defs app/src/atelierai/civitai; else echo "mypy not installed" && exit 1; fi

doctor:
	echo "AtelierAI environment doctor"
	echo ""
	printf "python:   "; command -v python || command -v python3 || true
	printf "pip:      "; python -m pip --version 2>/dev/null || python3 -m pip --version 2>/dev/null || echo "not available"
	printf "docker:   "; command -v docker || echo "not installed"
	printf "compose:  "; docker compose version 2>/dev/null || echo "not available"
	printf "ffmpeg:   "; command -v ffmpeg || echo "not installed"
	printf "exiftool: "; command -v exiftool || echo "not installed"
	printf "make:     "; command -v make || echo "not installed"
	printf "just:     "; command -v just || echo "not installed"

build-images:
	docker compose --profile dev build atelier-dev
	docker compose --profile runtime build atelier-runtime

test:
	python -m pytest -q app/tests

test-all:
	python -m pytest -q app/tests

test-bridge:
	python -m pytest -q app/tests/test_a1111_bridge_regression.py

compose-dev-up:
	docker compose --profile dev up -d atelier-dev

compose-dev-shell:
	docker compose exec atelier-dev bash

compose-dev-run:
	docker compose exec atelier-dev bash -lc 'cd /workspace && ./start.sh'

compose-runtime-build:
	docker compose --profile runtime build atelier-runtime

compose-runtime-up:
	docker compose --profile runtime up --build atelier-runtime

docker-clean:
	docker compose --profile dev --profile runtime down -v