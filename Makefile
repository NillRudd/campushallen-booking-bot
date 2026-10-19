.PHONY: dev test lint format typecheck migrate docker-up docker-down worker

dev:
	uvicorn campushallen.web.app:create_app --factory --reload --host 127.0.0.1

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

typecheck:
	mypy

migrate:
	alembic upgrade head

worker:
	campushallen-worker

docker-up:
	docker compose up --build

docker-down:
	docker compose down
