# SMBC APAC Genie — developer entry points. Override PROFILE/TARGET/SCALE on the command line.
PROFILE ?= DEFAULT
TARGET  ?= dev
PY      ?= .venv/bin/python
CLI     ?= databricks

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: venv
venv: ## Create the local venv and install dev deps
	uv venv --python 3.12 .venv
	uv pip install --python .venv/bin/python -e ".[dev]"

.PHONY: test
test: ## Run unit tests (no workspace needed)
	$(PY) -m pytest -q

.PHONY: itest
itest: ## Run integration tests (needs a live warehouse)
	SMBC_RUN_INTEGRATION=1 $(PY) -m pytest -q -m integration

.PHONY: auth-check
auth-check: ## Verify the Databricks profile is valid
	$(CLI) auth profiles | grep -E 'Name|$(PROFILE)'
	$(CLI) current-user me -p $(PROFILE) -o json | $(PY) -c 'import sys,json;print("user:",json.load(sys.stdin).get("userName"))'

.PHONY: discover
discover: ## Phase 0 read-only workspace checks
	PROFILE=$(PROFILE) bash scripts/discover.sh

.PHONY: validate
validate: ## Validate the bundle
	$(CLI) bundle validate -t $(TARGET) -p $(PROFILE)

.PHONY: deploy
deploy: ## Deploy the bundle (needs auth; Phase 2+)
	$(CLI) bundle deploy -t $(TARGET) -p $(PROFILE) --var="warehouse_id=$(WAREHOUSE_ID)"

.PHONY: build-dev
build-dev: ## Run build_all at SCALE=0.1
	$(CLI) bundle run smbc_genie_build_all -t dev -p $(PROFILE)

.PHONY: build-demo
build-demo: ## Run build_all at SCALE=1.0
	$(CLI) bundle run smbc_genie_build_all -t demo -p $(PROFILE)

.PHONY: phase
phase: ## Run one phase job, e.g. make phase N=3 (N = 2-6 or 8; no Genie job, see D50)
	$(CLI) bundle run smbc_genie_p0$(N)_$(word $(N),_ _ setup bronze silver gold metrics genie validate) -t $(TARGET) -p $(PROFILE)

.PHONY: teardown
teardown: ## Teardown dry run, read-only (docs/TEARDOWN.md shows how to execute)
	@echo "Dry run (read-only). To execute: $(PY) scripts/teardown.py --profile $(PROFILE) --execute (asks you to type the catalog name; see docs/TEARDOWN.md)"
	$(PY) scripts/teardown.py --profile $(PROFILE)
