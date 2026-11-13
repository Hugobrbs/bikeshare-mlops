.DEFAULT_GOAL := help
.PHONY: help install lint format test cov audit clean

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
