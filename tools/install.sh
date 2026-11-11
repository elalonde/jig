#!/bin/bash
# Install one version, and make it current when it is the newest version
# installed. The source is a clone, whose committed tree is installed, or a
# package file from make package, which holds that same tree of its release
# commit; jig upgrade installs a downloaded package this way.
# An installed version never changes under a project pinned to it, so a second
# run with the same content is a no-op and one with other content is refused.
#
# usage: install.sh <source-clone | package-file> <root> <version>
set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "usage: install.sh <source-clone | package-file> <root> <version>" >&2
    exit 2
fi
source=$1
root=$2
version=$3

if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "install: bad version: $version" >&2
    exit 2
fi

mkdir -p -- "$root"
staging=$(mktemp -d -- "$root/.install.XXXXXX")
link=$root/.current.$$
trap 'rm -rf -- "$staging" "$link"' EXIT

if [ -f "$source" ]; then
    tar -xzf "$source" -C "$staging"
    # A package always names its version, and one that names another version
    # would install under the wrong name.
    if ! [ -f "$staging/VERSION" ]; then
        echo "install: $source holds no VERSION, so it is not a package" >&2
        exit 1
    fi
else
    # git refuses a directory that is not a clone, which stops the install here.
    git -C "$source" archive HEAD | tar -x -C "$staging"
fi
if [ -f "$staging/VERSION" ] && [ "$(cat -- "$staging/VERSION")" != "$version" ]; then
    echo "install: $source is version $(cat -- "$staging/VERSION"), not $version" >&2
    exit 1
fi
# mktemp makes a private directory, and other accounts read this tree.
chmod 755 -- "$staging"

target=$root/$version
if [ -e "$target" ]; then
    # Byte code that Python left beside a tool is not content of the version.
    if ! diff -r -x __pycache__ -x '*.pyc' -- "$staging" "$target" >/dev/null
    then
        echo "install: $target exists with other content; a version is never replaced" >&2
        exit 1
    fi
    echo "install: $version already installed"
else
    mv -T -- "$staging" "$target"
    echo "install: $version installed under $root"
fi

# current points to the newest version, so a patch of an older line, which
# may come after a newer version, leaves it where it is.
newest=$(find "$root" -mindepth 1 -maxdepth 1 -type d \
    -regextype posix-extended -regex '.*/[0-9]+\.[0-9]+\.[0-9]+' \
    -printf '%f\n' | sort -V | tail -n 1)
if [ "$newest" != "$version" ]; then
    echo "install: current stays at $newest, which is newer than $version"
    exit 0
fi
# Renaming the link into place means current never points at nothing.
ln -s -- "$version" "$link"
mv -T -- "$link" "$root/current"
echo "install: current is $version"
