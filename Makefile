# Curbside AI — reproducible pipeline. `make help` lists targets.
# Full cold run: make data && make web. Nothing here deploys or pushes.

UV ?= uv
NPM ?= npm

.PHONY: help data fetch build reconcile qa prune forecast flows-ablation policy basemap web dev test lint check

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'

data: fetch build reconcile qa ## Download, build silver/gold, reconcile and write the QA report

fetch: ## Download zones + all TLC months in the window (skips verified files)
	$(UV) run curbside fetch-zones
	$(UV) run curbside fetch-range
	$(UV) run curbside build-zones

build: ## raw -> silver -> gold with invariant checks
	$(UV) run curbside build

reconcile: ## Compare monthly zone totals with TLC's aggregate (c5iv-bn4s)
	$(UV) run curbside reconcile

qa: ## Write reports/qa/qa_summary.json
	$(UV) run curbside qa

prune: ## Delete HVFHV raw files that passed every gate (policy D11)
	$(UV) run curbside prune-raw

forecast: ## Task A backtests, Task B backtest + freeze of the next unpublished month (~1-2 h)
	$(UV) run curbside forecast

flows-ablation: ## Graph-feature ablation for the forecaster (~1 h)
	$(UV) run curbside graph-ablation

policy: ## Congestion-pricing study (TLC data only)
	$(UV) run curbside policy

basemap: ## Self-hosted NYC basemap extract + fonts/sprites (~36 MB)
	$(UV) run curbside basemap

web: ## Build all static web artifacts and the production bundle
	$(UV) run curbside build-web
	cd web && $(NPM) ci && $(NPM) run build

dev: ## Local dev server at http://localhost:5173
	cd web && $(NPM) run dev

lint: ## Ruff + ESLint + TypeScript
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests
	cd web && $(NPM) run lint && $(NPM) run typecheck

test: ## Python unit tests (synthetic fixtures) + Playwright smoke tests (needs built data)
	$(UV) run pytest -q
	cd web && $(NPM) test

check: lint test ## Everything CI runs, plus browser tests
