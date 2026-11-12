.DEFAULT_GOAL := help
.PHONY: help install lint format test test-integration cov audit clean \
	up down ps logs train incident-data registry-status rollback loadtest-ui

PIPELINE = docker compose exec pipeline
TEST_DB = postgresql://postgres:test@127.0.0.1:55432/bikeshare_test

help: ## Affiche cette aide
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Installe l'environnement verrouillé (toutes les dépendances)
	uv sync --locked --all-extras --all-groups

lint: ## Vérifie le style et les erreurs statiques
	uv run ruff check .
	uv run ruff format --check .

format: ## Applique le formatage et les corrections automatiques
	uv run ruff check --fix .
	uv run ruff format .

test: ## Lance les tests unitaires
	uv run pytest -m "not integration"

test-integration: ## Lance les tests d'intégration sur un PostgreSQL jetable
	docker run -d --rm --name bikeshare-test-pg -p 127.0.0.1:55432:5432 \
		-e POSTGRES_PASSWORD=test -e POSTGRES_DB=bikeshare_test postgres:18.6-bookworm
	until docker exec bikeshare-test-pg pg_isready -q -U postgres; do sleep 1; done
	TEST_ADMIN_DATABASE_URL=$(TEST_DB) uv run pytest -m integration; \
		status=$$?; docker stop bikeshare-test-pg; exit $$status

cov: ## Lance les tests unitaires avec le rapport de couverture
	uv run pytest -m "not integration" --cov --cov-report=term-missing

audit: ## Cherche des vulnérabilités connues dans les dépendances verrouillées
	uv export --locked --all-extras --no-dev --no-emit-project --format requirements-txt \
		--output-file .audit-requirements.txt --quiet
	uv run pip-audit --requirement .audit-requirements.txt --disable-pip --strict; \
		status=$$?; rm -f .audit-requirements.txt; exit $$status

clean: ## Supprime les caches et fichiers générés par les outils
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} +

up: ## Démarre toute la stack (crée .env à partir de .env.example si besoin)
	@test -f .env || cp .env.example .env
	docker compose up -d --build

down: ## Arrête la stack (les volumes sont conservés)
	docker compose down

ps: ## Etat des services
	docker compose ps

logs: ## Suit les logs du pipeline et de l'API
	docker compose logs -f pipeline api

train: ## Déclenche tout de suite un cycle d'entraînement
	$(PIPELINE) prefect deployment run bikeshare-training/replay-mensuel

incident-data: ## Incident contrôlé : dépose un lot invalide puis lance le pipeline
	$(PIPELINE) bikeshare data publish --corrupt missing-hours
	$(PIPELINE) prefect deployment run bikeshare-training/replay-mensuel

registry-status: ## Versions du modèle, statut et alias
	$(PIPELINE) bikeshare registry status

rollback: ## Remet en service le champion précédent
	$(PIPELINE) bikeshare registry rollback --reason "retour arrière manuel"

loadtest-ui: ## Interface Locust sur http://localhost:8089
	uv run locust -f loadtests/locustfile.py MixedUser --host http://localhost:8001
