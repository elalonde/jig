"""Find the other clone of a project: the product clone of a -dev clone, and back.

A project has two clones in an account, side by side in one directory: the product
clone, named after the project, and the -dev clone, the same name with -dev added,
which holds the records about the product (specs, trace, reports, checks.tsv). So
the product clone of ~/ws/src/demo-dev is ~/ws/src/demo, and the -dev clone of
~/ws/src/demo is ~/ws/src/demo-dev.

Releases of jig before 0.6 kept the product clone inside the -dev clone, at
demo-dev/demo. That place is still found, so that a project whose clones have not
moved yet keeps working, but only when nothing sits at the place beside it: once the
clone beside it exists, a leftover copy inside the -dev clone is never used.

jig's pre-commit hook and the dispatcher bin/jig apply the same rule in their own
code, because the hook is copied into clones of accounts that cannot read jig's
tree, and the dispatcher must find the project before it knows which installed tree
to load. tests/test_clones.py runs one table of layouts on all three, so that the
copies stay equal.
"""

import pathlib

SUFFIX = "-dev"


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
        for candidate in (path.parent / project, path / project):
            if candidate.is_dir():
                return candidate
        return None
    if not name:
        return None
    beside = path.parent / (name + SUFFIX)
    if beside.is_dir():
        return beside
    # The place of the releases before 0.6: the -dev clone holds this clone.
    if path.parent.name == name + SUFFIX:
        return path.parent
    return None
