#!/bin/bash
# Point a clone's pushes at a fine-grained token, leaving fetches on its deploy
# key. Every setting is local to the clone, because the account's global git
# config and login may belong to another project.
#
# usage: push-on-token.sh <clone> <token-file>
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: push-on-token.sh <clone> <token-file>" >&2
    exit 2
fi
clone=$1
token_file=$(realpath -m -- "$2")

# The fetch URL is an ssh form, often through a host alias: <alias>:<owner>/<repo>.git
fetch_url=$(git -C "$clone" remote get-url origin)
slug=${fetch_url#*:}
slug=${slug#/}
if ! [[ "$slug" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    echo "push-on-token: cannot read owner/repo from origin: $fetch_url" >&2
    exit 2
fi
slug=${slug%.git}

# Single quotes in the path have to survive the shell git runs the helper in.
quoted_path=${token_file//\'/\'\\\'\'}

# An empty or missing token ends the attempt with quit=1. Otherwise git would
# fall through to a terminal prompt, which hangs an unattended session.
# The single quotes are literal text for the shell git starts later.
# shellcheck disable=SC2016
helper_body='test "$1" = get || exit 0; t=$(cat '"'$quoted_path'"' 2>/dev/null); if test -n "$t"; then printf "username=x-access-token\npassword=%s\n" "$t"; else echo quit=1; fi'

git -C "$clone" config --local remote.origin.pushurl "https://github.com/$slug.git"

# The empty entry clears every helper read so far, global ones included, so the
# helper after it is the only one left. Replacing all entries keeps a second
# run from stacking more of them.
git -C "$clone" config --local --unset-all credential.helper || true
git -C "$clone" config --local --add credential.helper ''
git -C "$clone" config --local --add credential.helper "!f() { $helper_body; }; f"
