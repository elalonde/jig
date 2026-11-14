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
