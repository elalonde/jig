#!/bin/bash
# Check comments and docs for private facts. The patterns are shared with the
# commit-msg hook, so they live in one Python module.
#
# usage: lint-a.sh [<root>]    LINT_TERMS names the private terms file.
set -euo pipefail

here=$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")
exec python3 "$here/../lib/lint_a.py" "$@"
