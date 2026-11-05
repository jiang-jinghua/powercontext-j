PYTHON ?= python3
export PYTHONPATH := $(CURDIR)/src

.PHONY: help setup init import context run-minimax run-manual status spec-update test reset demo

help:
	@echo "make setup | init | import | context | run-minimax | run-manual | status | spec-update | test | reset | demo"

setup:
	$(PYTHON) -m pip install -r requirements.txt

init:
	$(PYTHON) -m sddctx init

import:
	$(PYTHON) -m sddctx import

context:
	$(PYTHON) -m sddctx context T-001 --json

run-minimax:
	$(PYTHON) -m sddctx run T-001 --provider minimax

run-manual:
	$(PYTHON) -m sddctx run T-001 --provider manual --patch patches/T-001.patch

status:
	$(PYTHON) -m sddctx status T-001

spec-update:
	$(PYTHON) -m sddctx import --spec-version 2026.2 --spec-overlay demo/spec-updates/2026.2/promotion.md

test:
	$(PYTHON) -m pytest -q

reset:
	$(PYTHON) -m sddctx reset

demo:
	bash scripts/demo.sh
