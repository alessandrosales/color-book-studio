HOST ?= 127.0.0.1
PORT ?= 8000
EXTRAS ?= openai

.DEFAULT_GOAL := run
.PHONY: help setup run dev test lint typecheck check

help: ## Mostra os comandos disponíveis
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Instala dependências (ex: make setup EXTRAS="openai dev")
	uv sync $(foreach e,$(EXTRAS),--extra $(e))

run: ## Inicia o Color Book Studio (padrão: http://127.0.0.1:8000)
	uv run cbg --host $(HOST) --port $(PORT)

dev: ## Inicia o Color Book Studio com auto-reload
	uv run cbg --host $(HOST) --port $(PORT) --reload

test: ## Executa a suíte de testes
	uv run pytest

lint: ## Executa o Ruff
	uv run ruff check .

typecheck: ## Executa o Mypy
	uv run mypy src

check: lint typecheck test ## Executa lint, typecheck e testes
