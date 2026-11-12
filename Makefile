# Every tool is a required dependency: a missing one stops the check, so a green
# result always means every stage really ran.
SHELL := /bin/bash

.DEFAULT_GOAL := check
.NOTPARALLEL:
.PHONY: check deps hook test shellcheck lint-a real-start
.PHONY: version-bump package package-check source-archive

check: deps hook test shellcheck lint-a

deps:
	@for tool in git python3 shellcheck ssh-keygen tmux; do \
		command -v "$$tool" >/dev/null || { echo "make: $$tool is missing" >&2; exit 1; }; \
	done

# Setting the hooks path in this clone only also overrides any global one. An
# unpacked source archive is not a clone and makes no commits, so it skips this.
# The same path also runs the tracked pre-commit hook, which runs the term check
# of the -dev clone that holds this clone, so a clone of jig outside a -dev
# clone cannot commit after make hook.
hook:
	@if [ -e .git ]; then \
		git config --local core.hooksPath hooks; \
	else \
		echo "make: not a git clone, so no commit-msg hook to install"; \
	fi

test:
	python3 -m unittest discover -s tests -t .

shellcheck:
	@files=$$(python3 lib/lint_a.py --shell-files) && test -n "$$files" && shellcheck $$files

lint-a:
	tools/lint-a.sh

# Needs the real account, its tmux and its Claude Code login: a checks row, not in CI.
real-start:
	python3 -m unittest -v tests.real_start

# The release files. version-bump writes VERSION, which the release commit
# carries; the other three read the version from that commit and write into dist/.
# PACKAGE=<file> makes package-check read another package file.
version-bump:
	python3 lib/jig_package.py version-bump "$(VERSION)"

package:
	python3 lib/jig_package.py package

package-check:
	python3 lib/jig_package.py package-check $(PACKAGE)

source-archive:
	python3 lib/jig_package.py source-archive
