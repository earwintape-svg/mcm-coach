# G9: lowers the cost of every routine action and documents them in one
# place. This wraps the *existing* scripts/CLI (lan.sh, ship.sh, main.py's
# subcommands) -- it doesn't reimplement anything, so there's exactly one
# place each action's real logic lives. `make help` (the default target)
# lists everything below.

.DEFAULT_GOAL := help
.PHONY: help install run lan-install lan-status lan-restart lan-uninstall \
        notify-on notify-off test lint typecheck check backup verify-backup \
        restore export deploy clean lock

help: ## Show this list
	@echo "timely -- available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime + dev dependencies (editable, with dev extras)
	python3 -m pip install -e ".[dev]"

run: ## Run the dashboard locally on localhost:8765 (foreground, Ctrl+C to stop)
	python3 main.py serve

lan-install: ## Install the always-on background server (launchd) -- see lan.sh
	./lan.sh install

lan-status: ## Is the background server running? Show the phone URL + recent log.
	./lan.sh status

lan-restart: ## Re-sync code + restart the background server
	./lan.sh restart

lan-uninstall: ## Remove the background server
	./lan.sh uninstall

notify-on: ## Turn on daily briefing / log-nudge / weekly-review pushes
	./lan.sh notify-on

notify-off: ## Turn off scheduled pushes
	./lan.sh notify-off

test: ## Run the test suite (no network)
	python3 -m pytest -q

lint: ## Lint with ruff (same check as CI/pre-commit)
	ruff check .

typecheck: ## Type-check with mypy, pinned to 3.9 (same check as CI/pre-commit)
	mypy

check: test lint typecheck ## Run everything CI runs, locally, in one shot

backup: ## Take a manual encrypted backup snapshot now (G1/G3)
	python3 main.py backup

verify-backup: ## Verify the latest backup decrypts + passes integrity_check (G2)
	python3 main.py verify-backup

restore: ## Restore the live DB from the latest backup -- DESTRUCTIVE, prompts first (G9)
	python3 main.py restore

export: ## One-command full data export to plain JSON (G4)
	python3 main.py export-all

deploy: ## Ship: test -> rebuild demo -> commit -> push -> restart server (ship.sh)
	./ship.sh "$(MSG)"

lock: ## Regenerate requirements.lock, pinned + hashed for Python 3.9 (G8)
	uv pip compile --python-version 3.9 --generate-hashes requirements.txt -o requirements.lock

clean: ## Remove caches and other regenerable junk (never touches data/backups)
	rm -rf .pytest_cache .mypy_cache __pycache__ */__pycache__ */*/__pycache__ \
		*.egg-info src/*.egg-info build dist
