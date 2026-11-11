# Every tool is a required dependency: a missing one stops the check, so a
# green result always means every stage really ran.
SHELL := /bin/bash

.DEFAULT_GOAL := check
.NOTPARALLEL:
.PHONY: check deps hook test lint-a

check: deps hook test lint-a

deps:
	@for tool in git python3; do \
		command -v "$$tool" >/dev/null || { echo "make: $$tool is missing" >&2; exit 1; }; \
	done

# Setting the hooks path in this clone only also overrides any global one.
hook:
	git config --local core.hooksPath hooks

# The project adds its tests here.
test:
	@echo "make: no tests yet"

lint-a:
	tools/lint-a.sh
