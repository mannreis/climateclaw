.DEFAULT_GOAL := help
.PHONY: help benchmark

# Benchmark settings, override on the command line, e.g.
#   make benchmark BASE=v1.2.0 HEAD_REF=my-branch ROUNDS=5
BASE ?= main
HEAD_REF ?=
CONCURRENCY ?= 1,4,16,64
DURATION ?= 10
ROUNDS ?= 3
OUT ?= benchmark-report.md

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

benchmark: ## Benchmark BASE (main) vs HEAD_REF (working tree) and write $(OUT)
	uv run python benchmarks/main.py --base "$(BASE)" \
		$(if $(HEAD_REF),--head "$(HEAD_REF)") \
		-c "$(CONCURRENCY)" -d "$(DURATION)" --rounds "$(ROUNDS)" -o "$(OUT)"
