# shellcheck shell=bash
# jigfn: the client functions jigin, jigblock and jigshot, the channel from a
# person's machine to the orchestrators' host, with nothing pasted.
#
# Source this file from ~/.bashrc, or by path from a script. It only defines
# functions, so it loads in a non-interactive shell and under set -euo pipefail.
#
# Settings, as variables:
#   JIG_HOST     the orchestrators' host for scp, as [user@]host. No default:
#                each function refuses without it.
#   JIGSHOT_CMD  the screenshot command (default shot). It prints no path.
#   JIGSHOT_DIR  where that command saves images (default ~/Pictures/Screenshots).
#
#   jigin <project> <file>               copy a file to the project's inbox
#   jigblock <project> <ID> <hash>      fetch a block, check its hash, run it
#                                        with bash -l, and send the readback
#   jigshot <project> [<shot args>]      take a screenshot, send it to the inbox

# Print the scp host, or refuse when JIG_HOST is not set.
_jigfn_host() {
    if [ -z "${JIG_HOST:-}" ]; then
        echo "$1: JIG_HOST is not set" >&2
        return 1
    fi
    printf '%s' "$JIG_HOST"
}

# Refuse a project name that is not one word, so it cannot leave its path.
_jigfn_project() {
    [[ "${2:-}" =~ ^[a-z0-9][a-z0-9._-]*$ ]] && return 0
    echo "$1: bad project: ${2:-}" >&2
    return 1
}

# Send a file into a directory on the host as <name>. scp gives a new file
# the mode of its source, and a private one (mode 600, as screenshot tools
# save) would land where the orchestrator cannot read it; so the copy that
# goes is readable by all, as a file in the inbox should be.
_jigfn_send() { # <file> <host:directory/> <name>
    local t code=0
    t=$(mktemp -d) || return 1
    { cp -- "$1" "$t/$3" && chmod 644 -- "$t/$3" &&
        scp -- "$t/$3" "$2" </dev/null; } || code=$?
    rm -rf -- "$t"
    return "$code"
}

jigin() {
    local host
    [ $# -eq 2 ] || { echo "usage: jigin <project> <file>" >&2; return 2; }
    host=$(_jigfn_host jigin) || return 1
    _jigfn_project jigin "$1" || return 1
    [ -f "$2" ] || { echo "jigin: no file $2" >&2; return 1; }
    _jigfn_send "$2" "$host:/srv/jig/in/$1/" "${2##*/}"
}

jigshot() {
    local host p dir mark f n
    [ $# -ge 1 ] || { echo "usage: jigshot <project> [<shot args>]" >&2; return 2; }
    host=$(_jigfn_host jigshot) || return 1
    _jigfn_project jigshot "$1" || return 1
    p=$1
    shift
    dir=${JIGSHOT_DIR:-$HOME/Pictures/Screenshots}
    mark=$(mktemp) || return 1
    # A failed command does not stop the function: the image is what counts.
    "${JIGSHOT_CMD:-shot}" "$@" || echo "jigshot: ${JIGSHOT_CMD:-shot} exited $?" >&2
    # The command prints no path: take the newest image newer than the mark.
    f=$(find "$dir" -maxdepth 1 -type f -newer "$mark" \
        \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) \
        -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-) || f=
    rm -f -- "$mark"
    [ -n "$f" ] || { echo "jigshot: no new image in $dir; nothing sent" >&2; return 1; }
    n=shot-$(date +%Y%m%dT%H%M%S).${f##*.}
    _jigfn_send "$f" "$host:/srv/jig/in/$p/" "$n"
}

jigblock() {
    local host p id want t have codes
    [ $# -eq 3 ] || { echo "usage: jigblock <project> <ID> <hash>" >&2; return 2; }
    host=$(_jigfn_host jigblock) || return 1
    _jigfn_project jigblock "$1" || return 1
    p=$1
    id=$2
    want=${3,,}
    [[ "$id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
        { echo "jigblock: bad ID: $id" >&2; return 1; }
    [[ "$want" =~ ^[0-9a-f]{8}$ ]] ||
        { echo "jigblock: bad hash: $3 (8 hex digits)" >&2; return 1; }
    t=$(mktemp -d) || return 1
    scp -- "$host:/srv/jig/blocks/$p/$id.sh" "$t/" </dev/null ||
        { echo "jigblock: no block $id for $p" >&2; rm -rf -- "$t"; return 1; }
    # The hash is of the copy that runs, so a changed block cannot run.
    have=$(sha256sum -- "$t/$id.sh" | cut -c1-8)
    if [ "$have" != "$want" ]; then
        echo "jigblock: hash $have, expected $want; nothing ran" >&2
        rm -rf -- "$t"
        return 1
    fi
    # The caller may or may not have set -e and pipefail. On the left of ||,
    # a failed pipeline ends no caller, and PIPESTATUS is read either way.
    { bash -l "$t/$id.sh" 2>&1 | tee -- "$t/$id.txt"; codes=("${PIPESTATUS[@]}"); } || :
    # Readable by the orchestrator whatever this account's umask is.
    chmod 644 -- "$t/$id.txt" 2>/dev/null || :
    if ! scp -- "$t/$id.txt" "$host:/srv/jig/out/$p/" </dev/null; then
        echo "jigblock: readback not sent; it is in $t/$id.txt" >&2
        return 1
    fi
    rm -rf -- "$t"
    return "${codes[0]}"
}
# end jigfn
