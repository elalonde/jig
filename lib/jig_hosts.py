"""Read the host list, and check that ssh can get to a host before a step relies on it.

The host list is a TOML file that the host's administrator writes; jig itself holds no
machine's name or address. It has one [[host]] table for each machine, with three keys:

    name     the name that jig and its callers use for the machine
    address  what ssh connects to
    local    true when the machine is the one the orchestrators run on

A local machine is reached by its .local name, so its address has to end in ".local".
A machine that is not local may be anywhere, and jig never assumes it can be reached:
a step that needs one calls reach() first, which fails fast instead of hanging.

Every failure is a HostError whose message is meant for the person running the step.
"""

import os
import pathlib
import shlex
import signal
import subprocess
import tomllib

# Where the host list lives when a caller names no other file. Tests replace it.
HOSTS_FILE = "/srv/jig/hosts.toml"

# How long ssh gets to connect and run its command, in seconds.
REACH_SECONDS = 10

KEYS = ("name", "address", "local")


class HostError(Exception):
    """A host list that cannot be used, a host that is not in it, or one ssh cannot reach."""


def read_hosts(path=None):
    """Return {name: address} for every host in the list, after checking the whole file.

    A file with one bad table is refused as a whole, so a mistake shows up at the first
    use rather than at the first use of the host that carries it. Keys other than the
    three are left alone.
    """
    path = pathlib.Path(HOSTS_FILE if path is None else path)
    try:
        with open(path, "rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        raise HostError(f"host list {path} does not exist") from None
    except OSError as error:
        raise HostError(f"cannot read host list {path}: {error.strerror}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise HostError(f"host list {path} does not parse: {error}") from None
    try:
        return _check(data)
    except ValueError as error:
        raise HostError(f"host list {path} is not valid: {error}") from None


def _check(data):
    """Return {name: address} from the parsed file, or raise ValueError saying what is wrong."""
    tables = data.get("host", [])
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        raise ValueError('"host" is not an array of tables')
    hosts = {}
    for number, table in enumerate(tables, start=1):
        for key in KEYS:
            if key not in table:
                raise ValueError(f"host {number} has no key {key}")
        for key in ("name", "address"):
            if not isinstance(table[key], str):
                raise ValueError(f"host {number}: {key} is not a string")
            if not table[key]:
                raise ValueError(f"host {number}: {key} is empty")
        # ssh would read an address that starts with a dash as one of its options.
        if table["address"].startswith("-"):
            raise ValueError(f"host {number}: address starts with -")
        if not isinstance(table["local"], bool):
            raise ValueError(f"host {number}: local is not true or false")
        name, address = table["name"], table["address"]
        if name in hosts:
            raise ValueError(f"host name {name} appears twice")
        if table["local"] and not address.endswith(".local"):
            raise ValueError(f"host {name} is local, but its address {address} "
                             "does not end in .local")
        hosts[name] = address
    return hosts


def host_address(name, path=None):
    """Return the address of the named host, from HOSTS_FILE or from the file given."""
    path = HOSTS_FILE if path is None else path
    hosts = read_hosts(path)
    if name not in hosts:
        raise HostError(f"no host {name} in {path}")
    return hosts[name]


def reach(name, path=None, limit=REACH_SECONDS):
    """Check that ssh can log in to the named host and run a command; return its address.

    ssh runs in batch mode, so it never stops to ask for a password or a host key, and
    gets limit seconds both to connect and in all, so a host that answers but then
    stalls cannot hold the step either. JIG_SSH, when set, is the ssh command with any
    words of its own, as everywhere in jig; tests point it at a stand-in.
    """
    address = host_address(name, path)
    ssh = shlex.split(os.environ.get("JIG_SSH", "ssh"))
    command = [*ssh, "-o", "BatchMode=yes", "-o", f"ConnectTimeout={limit}", address, "true"]
    try:
        # A session of its own, so that a timeout stops ssh and anything it started.
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError:
        raise HostError(f"cannot reach host {name} at {address}") from None
    try:
        code = process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        code = None
    if code != 0:
        raise HostError(f"cannot reach host {name} at {address}")
    return address
