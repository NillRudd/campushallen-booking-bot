.PHONY: dev test lint format typecheck migrate docker-up docker-down worker browser-smoke i18n-extract i18n-update i18n-compile production-smoke

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

browser-smoke:
	python scripts/smoke_web.py

# gettext English source -> Swedish catalog. Review translations before compiling.
i18n-extract:
	pybabel extract --project "Campushallen Booker" --version 0.1.0 -F babel.cfg -k gettext_noop -k N_ -o src/campushallen/web/translations/messages.pot .

i18n-update: i18n-extract
	pybabel update --no-fuzzy-matching -i src/campushallen/web/translations/messages.pot -d src/campushallen/web/translations -l sv_SE

i18n-compile:
	pybabel compile --statistics -d src/campushallen/web/translations

production-smoke:
	python scripts/smoke_production.py
