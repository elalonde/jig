# jig

jig is a common build system for software that agent sessions write. It runs
each project with three accounts on one host: an orchestrator that plans the
work and writes prompts, a spec writer that writes the specs, and an
implementer that writes the code. Each piece of work is a unit: one fresh
session with one prompt, which ends with a committed report. jig starts the
units, waits for them, checks their reports, runs the checks of each project
and makes its releases.

## What is in the tree

- `bin/jig`: the command on the path. It runs a subcommand from the installed
  version that the project is pinned to, else from the newest one;
  `jig changelog` and `jig upgrade` always run from the newest one.
- `libexec/jig-<name>`: the subcommands, such as `jig start`, `jig wait`,
  `jig report` and `jig changelog`.
- `lib/`: the Python modules that the subcommands share.
- `tools/`: the install script, lint A, the CI check and the seeded-break
  runner.
- `hooks/commit-msg`: the commit message check that `make check` installs.
- `templates/`: the files that `jig new` writes into a new project, and the
  forms of a unit prompt and a unit report.
- `share/`: the client shell functions, and the git attributes with which
  `jig trace form` names the functions of a commit.
- `prefs.md`: the owner's standing preferences for messages to the owner.
- `CHANGELOG.md`: one entry for each version, newest first.

## Requirements

Debian 13 or a similar system with bash, git, Python 3.11 or newer, tmux,
OpenSSH and shellcheck. jig uses only the Python standard library.

## Build and test

    make            # the same as make check
    make check      # dependencies, commit-msg hook, tests, shellcheck, lint A

`make check` also runs in an unpacked source archive with no network access.
There it skips the commit-msg hook, because the tree is not a git clone, and
lint A reads the generic patterns only, because the private terms file is not
part of the source.

The hooks path that `make check` sets also runs the tracked `hooks/pre-commit`,
which runs the term check of the `-dev` clone beside this clone, `../jig-dev`
(or of the `-dev` clone that holds it, where a release before 0.6 put it), so a
clone of jig with no `-dev` clone cannot commit after `make hook`.

## Release files

    make version-bump VERSION=<X.Y.Z>   # write the version into VERSION
    make package                        # dist/jig-<X.Y.Z>.tar.gz
    make package-check                  # check the package
    make source-archive                 # dist/jig-<X.Y.Z>-src.tar.gz

The package holds the tree of the release commit. The source archive holds the
same files under the directory `jig-<X.Y.Z>/`. Both take the version from the
file `VERSION` of the commit, so commit the version bump first.

## Install

Each version is installed under `/opt/jig/<X.Y.Z>/`, and `/opt/jig/current`
points to the newest one:

    tools/install.sh <clone or package file> /opt/jig <X.Y.Z>

An installed version never changes: a second install of the same content does
nothing, and one with other content is refused. `jig changelog --since <X.Y.Z>`
prints the change log entries of the versions after the one a project uses.

`jig upgrade <X.Y.Z>`, run as the account that owns `/opt/jig`, downloads the
GitHub release `v<X.Y.Z>`, checks every file against its `SHA256SUMS` and
installs the package with the install script. `jig upgrade <project> <X.Y.Z>`
also pins the project to that version in the conf file of each of its
accounts, and refuses while a unit of the project runs. Both always run from
the newest installed version, whatever the project is pinned to. When the
newest installed version has no `jig upgrade` yet, run it from a clone of jig
instead: `libexec/jig-upgrade <X.Y.Z>`.

## License

MIT, in `LICENSE`. `THIRD-PARTY-NOTICES` lists the software of others that jig
uses, and `SOURCE-OFFER` says where to get the source of each release.
