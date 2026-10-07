# =============================================================================
# Healthcare Readmission DevSecMLOps — developer commands
#   make help        list targets
#   make ci          what CI runs: lint + type-check + test + security-scan
# =============================================================================
SHELL := /bin/bash
.DEFAULT_GOAL := help

PY      ?= .venv/bin/python
BIN     := .venv/bin
IMAGE   ?= healthcare-readmission-api
TAG     ?= latest
SRC_DIRS := api src pipelines scripts

# Load dev environment (API_KEYS, PIPELINE_API_KEY, ...) so Lab 6 pipeline RBAC
# works locally. .env wins; otherwise fall back to the fake demo keys.
ifneq (,$(wildcard .env))
include .env
export
else ifneq (,$(wildcard .env.example))
include .env.example
export
endif

.PHONY: help install generate-data validate-data preprocess train evaluate test \
        test-unit test-integration test-security lint format type-check security-scan dast \
        docker-build docker-up docker-down run-api monitor retrain clean ci pre-commit

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv and install all pinned dependencies
	test -d .venv || python3.11 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

pre-commit: ## Install git pre-commit hooks
	$(BIN)/pre-commit install

# ---------------------------------------------------------------- ML pipeline
generate-data: ## Generate the synthetic dataset (data/raw/)
	$(PY) -m scripts.generate_dataset

validate-data: ## Run data validation (RBAC: pipeline:validate)
	$(PY) -m pipelines.validation_pipeline

preprocess: ## Stratified train/test split (data/processed/)
	$(PY) -m scripts.preprocess

train: ## Train + compare models, save artifacts to models/ (RBAC: pipeline:train)
	$(PY) -m pipelines.training_pipeline

evaluate: ## Evaluate the saved model, write reports/evaluation + figures
	$(PY) -m scripts.evaluate

monitor: ## Evidently drift report -> reports/drift/
	$(PY) -m pipelines.monitoring_pipeline

retrain: ## Drift-aware retraining (RBAC: pipeline:retrain)
	$(PY) -m pipelines.retraining_pipeline

# ---------------------------------------------------------------- quality
test: ## All tests with coverage (reports/coverage_html)
	$(PY) -m pytest --cov --cov-report=term-missing --cov-report=html --cov-report=xml

test-unit: ## Unit tests only
	$(PY) -m pytest tests/unit -q

test-integration: ## Integration tests only
	$(PY) -m pytest tests/integration -q

test-security: ## Security tests only
	$(PY) -m pytest tests/security -q

lint: ## Ruff lint + format check
	$(BIN)/ruff check $(SRC_DIRS) tests
	$(BIN)/ruff format --check $(SRC_DIRS) tests

format: ## Auto-fix lint issues and format
	$(BIN)/ruff check --fix $(SRC_DIRS) tests
	$(BIN)/ruff format $(SRC_DIRS) tests

type-check: ## mypy on src/ and api/
	$(BIN)/mypy src api

security-scan: ## Bandit + pip-audit (+ gitleaks, trivy, checkov if installed)
	@echo "== Bandit (Python SAST) =="
	$(BIN)/bandit -c pyproject.toml -r $(SRC_DIRS) -ll -ii
	@echo "== pip-audit (dependency CVEs) =="
	$(BIN)/pip-audit -r requirements-api.txt
	@echo "== Gitleaks (secrets) =="
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks dir . --config .gitleaks.toml --redact --exit-code 1; \
	else echo "gitleaks not installed - skipped (brew install gitleaks)"; fi
	@echo "== Trivy (filesystem CVEs + secrets, fail on CRITICAL) =="
	@if command -v trivy >/dev/null 2>&1; then \
		trivy fs --scanners vuln,secret --severity CRITICAL --ignore-unfixed --skip-dirs .venv --exit-code 1 .; \
	else echo "trivy not installed - skipped (brew install trivy)"; fi
	@echo "== Checkov (IaC) =="
	@if command -v checkov >/dev/null 2>&1; then \
		checkov -d . --framework terraform,kubernetes,dockerfile,github_actions --skip-path .venv --compact --quiet; \
	else echo "checkov not installed - skipped (pipx install checkov)"; fi

# DAST: OWASP ZAP attacks the RUNNING API over HTTP. Start the API first, e.g.
#   ENV=development RATE_LIMIT=100000/minute $(PY) -m uvicorn api.main:app --port 8090
# then:  make dast DAST_PORT=8090
# ZAP runs in Docker and reaches the host API via host.docker.internal (Docker
# Desktop). Reports land in reports/dast/. Fails only on HIGH-risk alerts.
DAST_PORT    ?= 8090
DAST_TARGET  ?= http://host.docker.internal:$(DAST_PORT)/openapi.json
DAST_API_KEY ?= demo-admin-key-change-me
ZAP_IMAGE    ?= ghcr.io/zaproxy/zaproxy:stable

dast: ## OWASP ZAP API scan (Docker) of a running API on DAST_PORT (default 8090)
	@if ! docker info >/dev/null 2>&1; then \
		echo "Docker daemon not running - start Docker Desktop, then re-run 'make dast'"; exit 1; fi
	@curl -sf http://127.0.0.1:$(DAST_PORT)/openapi.json >/dev/null || \
		(echo "No API with /openapi.json on :$(DAST_PORT) - start it with ENV!=production first" && exit 1)
	mkdir -p reports/dast && chmod a+w reports/dast
	-docker run --rm -v "$(CURDIR)":/zap/wrk:rw --add-host=host.docker.internal:host-gateway \
		-e ZAP_AUTH_HEADER=X-API-Key -e ZAP_AUTH_HEADER_VALUE="$(DAST_API_KEY)" \
		-e ZAP_AUTH_HEADER_SITE=host.docker.internal \
		$(ZAP_IMAGE) zap-api-scan.py -t $(DAST_TARGET) -f openapi -a \
		-c .zap/rules.tsv -J reports/dast/zap_report.json \
		-r reports/dast/zap_report.html -w reports/dast/zap_report.md
	$(PY) -m scripts.zap_gate reports/dast/zap_report.json --rules .zap/rules.tsv

ci: lint type-check test security-scan ## Everything CI runs

# ---------------------------------------------------------------- run / docker
run-api: ## Run the API locally with auto-reload on :8000
	$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

docker-build: ## Build the API image (needs models/ artifacts: run `make train` first)
	docker build -t $(IMAGE):$(TAG) .

docker-up: ## Start api + mlflow + prometheus + grafana
	@test -f .env || (echo "Create .env first: cp .env.example .env" && exit 1)
	docker compose up -d --build

docker-down: ## Stop the stack
	docker compose down

clean: ## Remove caches and generated reports (keeps data and models)
	find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov \
	       reports/coverage_html reports/coverage.xml reports/junit.xml *.sarif sbom*.json
