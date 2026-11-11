"""Make and check jig's release files: the version file, the package and the source archive.

The package is the tree of the release commit as git archive gives it, so an
install from the package gives the same files as an install from a clone of
that commit. The source archive holds the same files under one directory named
for the version, the form people expect when they unpack source. Both are
written into dist/, which git ignores. The version comes from the file VERSION
of the release commit, so a package always names the commit it was made from.

The Makefile runs this module; it is not a command of its own.

usage: python3 lib/jig_package.py version-bump <X.Y.Z>
       python3 lib/jig_package.py package | source-archive
       python3 lib/jig_package.py package-check [<package file>]
"""

import pathlib
import re
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION_FORM = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
PACKAGE_NAME = re.compile(r"jig-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz")

# The files a release must carry at the top of its package: the license, the
# notices and the offer that make it fit to publish, the change log that jig
# changelog reads after an install, the owner's preferences that jig new copies,
# and the version itself.
RELEASE_FILES = ("README.md", "LICENSE", "THIRD-PARTY-NOTICES", "SOURCE-OFFER",
                 "CHANGELOG.md", "prefs.md", "VERSION")


class Failed(Exception):
    """A release file cannot be made, or a package is not fit to publish."""


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                            check=False)
    if result.returncode != 0:
        raise Failed(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def version_bump(root, version):
    if not VERSION_FORM.fullmatch(version):
        raise Failed(f"version-bump needs VERSION=<X.Y.Z>, not {version!r}")
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    return f"VERSION is {version}"


def committed_version(root):
    """Return the version of HEAD, refusing one that the work tree has changed.

    A bump that is not yet committed would give a package whose name and VERSION
    disagree, so it is refused here rather than found by package-check.
    """
    result = subprocess.run(["git", "-C", str(root), "show", "HEAD:VERSION"],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise Failed("the commit HEAD has no VERSION: run make version-bump and commit it")
    version = result.stdout.strip()
    if not VERSION_FORM.fullmatch(version):
        raise Failed(f"VERSION of HEAD is not <X.Y.Z>: {version!r}")
    try:
        in_tree = (root / "VERSION").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        in_tree = None
    if in_tree != version:
        raise Failed(f"VERSION in the work tree differs from HEAD ({version}): commit it first")
    return version


def archive(root, name, prefix=None):
    version = committed_version(root)
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / name.format(version=version)
    options = [f"--prefix=jig-{version}/"] if prefix else []
    git(root, "archive", "--format=tar.gz", *options, "-o", str(target), "HEAD")
    return f"wrote {target.relative_to(root)}"


def package(root):
    return archive(root, "jig-{version}.tar.gz")


def source_archive(root):
    return archive(root, "jig-{version}-src.tar.gz", prefix=True)


def package_problems(path):
    """Return the reasons a package file is not fit to publish, or an empty list."""
    match = PACKAGE_NAME.fullmatch(path.name)
    if not match:
        return [f"{path.name}: the name is not jig-<X.Y.Z>.tar.gz"]
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
            files = {}
            problems = []
            for member in members:
                name = member.name.removeprefix("./").rstrip("/")
                # An entry that leaves the install directory would write outside it.
                if name.startswith("/") or ".." in name.split("/"):
                    problems.append(f"{path.name}: entry outside the tree: {member.name}")
                if member.isfile():
                    files[name] = member
            for wanted in RELEASE_FILES:
                if wanted not in files:
                    problems.append(f"{path.name}: {wanted} is missing at the top")
            if "VERSION" in files:
                text = tar.extractfile(files["VERSION"]).read().decode("utf-8", "replace")
                if text.strip() != match.group(1):
                    problems.append(f"{path.name}: VERSION holds {text.strip()!r}, "
                                    f"not {match.group(1)}")
    except (OSError, tarfile.TarError, EOFError) as error:
        return [f"{path}: cannot read: {error}"]
    return problems


def package_check(root, path=None):
    if path is None:
        path = root / "dist" / f"jig-{committed_version(root)}.tar.gz"
    path = pathlib.Path(path)
    problems = package_problems(path)
    if problems:
        raise Failed("\n".join(problems))
    return f"{path.name}: ok"


def main(argv, root=ROOT):
    commands = {
        ("version-bump", 1): lambda: version_bump(root, argv[1]),
        ("package", 0): lambda: package(root),
        ("source-archive", 0): lambda: source_archive(root),
        ("package-check", 0): lambda: package_check(root),
        ("package-check", 1): lambda: package_check(root, argv[1]),
    }
    command = commands.get((argv[0], len(argv) - 1)) if argv else None
    if command is None:
        print("usage: " + __doc__.split("usage:", 1)[1].strip(), file=sys.stderr)
        return 2
    try:
        print(f"{argv[0]}: {command()}")
    except Failed as error:
        for line in str(error).splitlines():
            print(f"{argv[0]}: {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
