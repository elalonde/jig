"""The directories a session may read outside its repo, per role.

Written as the worker account sees them: ~ is the home of that account, so whoever
uses a list expands it there. A list says what a role may get; a directory that does
not exist in the account is not passed to the session, which is the work of jig start.
"""

# While a project's clean room is on, no prompt names a path under this one, for any
# role: the reference reaches the other roles only through the spec. jig validate
# prompt reads the clean room from the project profile (see lib/jig_profile.py).
REFERENCE_ROOT = "~/ws/ref"

_SHARED = ("~/ws/prompts/{project}", "~/ws/reviews/{project}")
# A worker account that has the product clone gets it too. It sits beside the -dev
# clone, outside the repo the session works in, so it must be in the list; in an
# account without one, jig start passes nothing for it. The orchestrator reads the
# -dev repo and not the product, so its list does not hold it.
_WORKER = (*_SHARED, "~/ws/src/{project}")
_APPROVED = "~/ws/src/{project}-dev-approved"
_REFERENCE = REFERENCE_ROOT + "/{project}"

_DIRECTORIES = {
    "spec": (*_WORKER, _REFERENCE),
    "impl": (*_WORKER, _APPROVED),
    # A review in the implementer account gets the spec and the code; the reference
    # stays with the spec writer.
    "rev": (*_WORKER, _APPROVED),
    "orch": (*_SHARED, "/srv/jig/in/{project}", "/srv/jig/out/{project}",
             "/srv/jig/blocks/{project}"),
}


def of_role(role, project):
    """Return the list of a role in a project; a role jig does not know has none."""
    return [path.format(project=project) for path in _DIRECTORIES.get(role, ())]


# The folders that a project adds for the sessions of each account live in its
# kickoff record (see lib/jig_profile.py), in a section that starts with this line
# and ends at the next line that starts with "## ". Each line of the form
# "<account>: <folder>" adds one folder for one of these three accounts; every other
# line is prose. The folder is absolute, or starts with ~/ for the home of that account.
DIRECTORIES_HEADING = "## Directories"
ACCOUNTS = ("orchestrator", "specwriter", "implementer")
# The key of the conf file that names the account of each word above.
ACCOUNT_KEYS = {"orchestrator": "ORCH_ACCOUNT", "specwriter": "SPEC_ACCOUNT",
                "implementer": "IMPL_ACCOUNT"}
# The roles of jig start and of the supervisor, with the key that names their accounts.
ROLE_KEYS = (("orch", "ORCH_ACCOUNT"), ("spec", "SPEC_ACCOUNT"), ("impl", "IMPL_ACCOUNT"),
             ("rev", "REV_ACCOUNT"))
UNREADABLE = ("jig: cannot read line {number} of the section Directories of the kickoff "
              "record; line ignored")
# Where the paths of the orchestrator's list point when the host keeps its jig root
# elsewhere, as tests do.
SRV_ROOT = "/srv/jig"


def with_srv(paths, root):
    """Return the paths with the jig root /srv/jig replaced by root, when it is given."""
    if not root:
        return list(paths)
    return [root + path[len(SRV_ROOT):] if path.startswith(SRV_ROOT + "/") else path
            for path in paths]


def absolute(folder, home):
    """Return a folder as jig compares it: ~/ read in home, with no / at the end.

    With no home, a folder under ~/ stays as it is, so that two such folders still
    compare equal when they name the same place.
    """
    if folder.startswith("~/") and home:
        folder = home.rstrip("/") + folder[1:]
    return folder.rstrip("/") or "/"


def holds(outer, inner):
    """Say whether the folder outer is inner or a folder above it."""
    return inner == outer or inner.startswith(outer.rstrip("/") + "/")


def kept_from(account, homes, project):
    """Return the folders that no line may give an account, in every home.

    The reference, ~/ws/ref, stays with the spec writer, and the product clone stays
    away from the orchestrator, who reads only the -dev repo. Each holds in the home
    of every account, so that no line reaches another account's copy by its path.
    """
    kept = []
    for home in (home for home in homes.values() if home):
        if account != "specwriter":
            kept.append(absolute("~/" + REFERENCE_ROOT[2:], home))
        if account == "orchestrator":
            kept.append(absolute(f"~/ws/src/{project}", home))
    return kept


def read_line_folder(account, folder, homes, project):
    """Return the absolute folder of one line, or None when jig cannot read it."""
    if not folder.startswith(("/", "~/")):
        return None
    rest = folder[2:] if folder.startswith("~/") else folder[1:]
    if "//" in folder or any(part in (".", "..") for part in rest.split("/")):
        return None
    home = homes.get(account)
    if folder.startswith("~/") and not home:
        return None
    found = absolute(folder, home)
    # A folder above a kept one would give the kept one through its parent.
    if any(holds(kept, found) or holds(found, kept)
           for kept in kept_from(account, homes, project)):
        return None
    return found


def read_directories(text, homes, project):
    """Read the section Directories of a kickoff record.

    text is the record, or None when there is none to read; homes maps each account
    word of ACCOUNTS to its home, or to None when it has none. Return the folders of
    each account, absolute and each once, in the order of the lines, and one
    UNREADABLE line for each line of the form that jig cannot read. jig follows no
    symbolic link here: the owner's approval of the record covers a link.
    """
    folders = {account: [] for account in ACCOUNTS}
    problems = []
    if text is None:
        return folders, problems
    inside = False
    for number, line in enumerate(text.split("\n"), start=1):
        if line.startswith("## "):
            inside = line.rstrip() == DIRECTORIES_HEADING
            continue
        account, colon, folder = line.partition(":")
        if not inside or not colon or account not in ACCOUNTS:
            continue
        found = read_line_folder(account, folder.strip(), homes, project)
        if found is None:
            problems.append(UNREADABLE.format(number=number))
        elif found not in folders[account]:
            folders[account].append(found)
    return folders, problems


def homes_of(conf):
    """Map each account word to the home of the account that the conf file names for it.

    The account database gives the home; an account that it does not know has None.
    """
    import pwd  # only the readers of the section need it

    homes = {}
    for account, key in ACCOUNT_KEYS.items():
        name = (conf.get(key) or account).split()[0]
        try:
            homes[account] = pwd.getpwnam(name).pw_dir
        except KeyError:
            homes[account] = None
    return homes


def word_of(conf, account):
    """Return the account word of ACCOUNTS whose account in the conf file is account."""
    for word, key in ACCOUNT_KEYS.items():
        if account in (conf.get(key) or word).split():
            return word
    return None


def approved_list(conf, account, project, folders, srv_root=None):
    """Return the folders that a session in an account may get, as written.

    That is the -dev clone, the list of each role that runs in the account by the
    conf file, the orchestrator's list with the jig root of srv_root, and the folders
    of the account in the section Directories, which come absolute. A conf file
    without the key of one of the three accounts gives that account its own word,
    as homes_of does, so that a hand-written conf file without ORCH_ACCOUNT still
    gives the orchestrator account its own list.
    """
    defaults = {key: word for word, key in ACCOUNT_KEYS.items()}
    listed = [f"~/ws/src/{project}-dev"]
    for role, key in ROLE_KEYS:
        if account in (conf.get(key) or defaults.get(key, "")).split():
            listed.extend(with_srv(of_role(role, project), srv_root))
    return [*listed, *folders]


def first_outside(candidates, approved, home):
    """Return the first candidate folder that is not in the approved list, or None."""
    allowed = {absolute(folder, home) for folder in approved}
    for folder in candidates:
        if absolute(folder, home) not in allowed:
            return folder
    return None
