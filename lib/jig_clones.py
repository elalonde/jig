"""Find the other clone of a project: the product clone of a -dev clone, and back.

A project has two clones in an account, side by side in one directory: the product
clone, named after the project, and the -dev clone, the same name with -dev added,
which holds the records about the product (specs, trace, reports, checks.tsv). So
the product clone of ~/ws/src/demo-dev is ~/ws/src/demo, and the -dev clone of
~/ws/src/demo is ~/ws/src/demo-dev.

A product clone can have linked work trees (git worktree add) in any folder. Such a
work tree has the -dev clone of its main work tree: the folder that holds the .git
directory that all work trees of the repository share. git names that directory
with rev-parse --git-common-dir. When its name is not .git, as in a bare repository
or one whose git directory was moved elsewhere, the repository has no main work tree
and so no -dev clone. Outside any git repository, the rule by the path above holds.

jig's two term hooks and the dispatcher bin/jig apply the same rule in their own
code, because the hooks are copied into clones of accounts that cannot read jig's
tree, and the dispatcher must find the project before it knows which installed tree
to load. The dispatcher keeps only the rule by the path. tests/test_clones.py runs
one table of layouts on all three, so that the copies stay equal.
"""

import os
import pathlib
import subprocess

SUFFIX = "-dev"
# The name of the git directory inside a main work tree.
GIT_DIR_NAME = ".git"


def git_common_dir(path):
    """Return the git directory that all work trees of the repository at path share.

    Return None when path is in no git repository. The variables that git sets for a
    hook are left out, because they would name the repository of the hook instead of
    the one at path.
    """
    try:
        listed = subprocess.run(["git", "rev-parse", "--local-env-vars"],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                check=False)
        hidden = set(listed.stdout.split())
        env = {key: value for key, value in os.environ.items() if key not in hidden}
        found = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, check=False)
    except OSError:
        return None
    named = found.stdout.strip()
    if found.returncode != 0 or not named:
        return None
    return pathlib.Path(named).resolve()


def other_clone(clone):
    """Return the other clone of the clone at path clone, or None when there is none.

    A clone whose directory name ends in -dev is a -dev clone, and the result is its
    product clone; any other clone is a product clone, and the result is its -dev
    clone. A project name never ends in -dev, so the name alone tells the two apart.
    """
    path = pathlib.Path(clone).resolve()
    name = path.name
    if name.endswith(SUFFIX):
        project = name[:-len(SUFFIX)]
        if not project:
            return None
        beside = path.parent / project
        return beside if beside.is_dir() else None
    common = git_common_dir(path) if path.is_dir() else None
    if common is not None:
        if common.name != GIT_DIR_NAME:
            return None
        path = common.parent
        name = path.name
    if not name:
        return None
    beside = path.parent / (name + SUFFIX)
    return beside if beside.is_dir() else None
