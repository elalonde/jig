import hashlib
import http.server
import io
import json
import os
import shutil
import tarfile
import threading
import unittest
import urllib.parse

from tests.support import ROOT, TempTestCase, run
from tests.test_ci_green import loopback_works
from tests.units_support import SSH_DOUBLE

UPGRADE = ROOT / "libexec" / "jig-upgrade"
INSTALL = ROOT / "tools" / "install.sh"
SLUG = "fixture-owner/jig"
ORCH = "orchestrator"
WORKERS = ("specwriter", "implementer")


def package_bytes(version, marker="first"):
    """A package as make package lays it out: the tree at the top, with VERSION."""
    files = {
        "VERSION": f"{version}\n",
        "bin/jig": (ROOT / "bin" / "jig").read_text(encoding="utf-8"),
        "libexec/jig-probe": f'#!/bin/bash\necho "{version} {marker}"\n',
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o755 if name != "VERSION" else 0o644
            member.mtime = 0
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def sums_text(files):
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                   for name, data in sorted(files.items()))


class FakeReleases(http.server.ThreadingHTTPServer):
    """Answer the release by tag and the asset endpoint of the API, for one repo."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeReleasesHandler)
        self.slug = SLUG
        self.releases = {}
        self.requests = []

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def publish(self, version, marker="first", served=None):
        """Make the release v<version>; served replaces a file's bytes after the sums."""
        package = package_bytes(version, marker)
        files = {f"jig-{version}.tar.gz": package,
                 f"jig-{version}-src.tar.gz": b"source archive of " + version.encode()}
        files["SHA256SUMS"] = sums_text(files).encode()
        files.update(served or {})
        self.releases[version] = files


class FakeReleasesHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, data, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        api = self.server
        path = urllib.parse.urlparse(self.path).path
        api.requests.append((path, self.headers.get("Accept"),
                             self.headers.get("Authorization")))
        tag_prefix = f"/repos/{api.slug}/releases/tags/v"
        asset_prefix = f"/repos/{api.slug}/releases/assets/"
        assets = [(version, name) for version, files in sorted(api.releases.items())
                  for name in files]
        if path.startswith(tag_prefix) and path[len(tag_prefix):] in api.releases:
            version = path[len(tag_prefix):]
            body = {"tag_name": f"v{version}", "assets": [
                {"id": number, "name": name, "url": f"{api.url}{asset_prefix}{number}"}
                for number, (owner, name) in enumerate(assets) if owner == version]}
            self.answer(json.dumps(body).encode(), "application/json")
        elif path.startswith(asset_prefix) and path[len(asset_prefix):].isdigit() \
                and int(path[len(asset_prefix):]) < len(assets):
            version, name = assets[int(path[len(asset_prefix):])]
            # The real endpoint answers the bytes only to this Accept, and the
            # asset's description otherwise.
            if self.headers.get("Accept") != "application/octet-stream":
                self.answer(json.dumps({"name": name}).encode(), "application/json")
                return
            self.answer(api.releases[version][name], "application/octet-stream")
        else:
            self.send_error(404)


@unittest.skipUnless(loopback_works(), "no loopback network for the fake API")
class UpgradeTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.api = FakeReleases()
        thread = threading.Thread(target=self.api.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        self.addCleanup(self.api.server_close)
        self.addCleanup(self.api.shutdown)
        self.root = self.tmp / "opt"
        self.locks = self.tmp / "lock"
        self.locks.mkdir()
        self.fake = self.tmp / "fake"
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.ssh = self.bin_dir / "ssh-double"
        self.ssh.write_text(SSH_DOUBLE, encoding="utf-8")
        self.ssh.chmod(0o755)

    def upgrade(self, *args, tool=UPGRADE, **extra):
        env = self.env(JIG_ROOT=str(self.root), JIG_GITHUB_API=self.api.url,
                       JIG_UPGRADE_REPO=SLUG, JIG_SSH=str(self.ssh),
                       JIG_LOCK_DIR=str(self.locks), FAKE=str(self.fake))
        env.update(extra)
        return run([tool, *args], env=env, cwd=self.tmp)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def current(self):
        return os.readlink(self.root / "current")

    def conf_path(self, account):
        home = self.home if account == ORCH else self.fake / "accounts" / account
        return home / ".config" / "jig" / "demo.conf"

    def project(self, worker_text="REPO=demo\nBRANCH=0.1\nJIG_VERSION=0.3.0\n"):
        """Conf files of the fixture project demo in the orchestrator and two workers.

        The reviewer accounts repeat the two workers, so each account appears twice in
        the orchestrator's conf file.
        """
        self.conf_path(ORCH).parent.mkdir(parents=True)
        self.conf_path(ORCH).write_text(
            "REPO=demo\nBRANCH=0.1\nJIG_VERSION=0.3.0\n"
            f"ORCH_ACCOUNT={ORCH}\nSPEC_ACCOUNT=specwriter\nIMPL_ACCOUNT=implementer\n"
            "REV_ACCOUNT=specwriter implementer\n", encoding="utf-8")
        for account in WORKERS:
            self.conf_path(account).parent.mkdir(parents=True)
            self.conf_path(account).write_text(worker_text, encoding="utf-8")

    def confs(self):
        return {account: self.conf_path(account).read_text(encoding="utf-8")
                for account in (ORCH, *WORKERS)}

    def test_upgrade_installs_a_release_and_points_current_to_it(self):
        # A published release with a package, a source archive and their sums is
        # fetched with no login from the asset endpoint, and installed as current.
        self.api.publish("0.4.0")
        result = self.upgrade("0.4.0")
        self.assert_ok(result)
        self.assertEqual((self.root / "0.4.0" / "VERSION").read_text(), "0.4.0\n")
        probe = run([self.root / "0.4.0" / "libexec" / "jig-probe"], env=self.env())
        self.assertEqual(probe.stdout, "0.4.0 first\n")
        self.assertEqual(self.current(), "0.4.0")
        self.assertEqual({auth for _, _, auth in self.api.requests}, {None})
        downloads = [accept for path, accept, _ in self.api.requests if "/assets/" in path]
        self.assertEqual(downloads, ["application/octet-stream"] * 3)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ["0.4.0", "current"])

    def test_a_file_whose_hash_does_not_match_is_refused_and_nothing_is_installed(self):
        # The served package differs from the one its sums were made from.
        self.api.publish("0.4.0", served={"jig-0.4.0.tar.gz": package_bytes("0.4.0", "other")})
        result = self.upgrade("0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("jig-0.4.0.tar.gz does not match its hash in SHA256SUMS",
                      result.stderr)
        self.assertFalse(self.root.exists())

    def test_a_source_archive_whose_hash_does_not_match_is_refused_too(self):
        self.api.publish("0.4.0", served={"jig-0.4.0-src.tar.gz": b"changed"})
        result = self.upgrade("0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("jig-0.4.0-src.tar.gz does not match", result.stderr)
        self.assertFalse(self.root.exists())

    def test_a_release_file_that_the_sums_do_not_list_is_refused(self):
        self.api.publish("0.4.0", served={"extra.txt": b"not listed"})
        result = self.upgrade("0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not listed ['extra.txt']", result.stderr)
        self.assertFalse(self.root.exists())

    def test_a_version_with_no_release_is_refused(self):
        result = self.upgrade("0.9.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{SLUG} has no release v0.9.0", result.stderr)

    def test_current_points_to_the_newest_also_when_an_older_version_comes_after(self):
        # 0.5.0 goes in after 0.4.0 and becomes current; 0.4.1, a patch of the older
        # line installed last, leaves current at 0.5.0.
        for version in ("0.4.0", "0.5.0", "0.4.1"):
            self.api.publish(version)
        self.assert_ok(self.upgrade("0.4.0"))
        self.assert_ok(self.upgrade("0.5.0"))
        self.assertEqual(self.current(), "0.5.0")
        result = self.upgrade("0.4.1")
        self.assert_ok(result)
        self.assertTrue((self.root / "0.4.1" / "VERSION").is_file())
        self.assertEqual(self.current(), "0.5.0")

    def test_the_same_version_again_with_the_same_content_is_no_change(self):
        self.api.publish("0.4.0")
        self.assert_ok(self.upgrade("0.4.0"))
        probe = self.root / "0.4.0" / "libexec" / "jig-probe"
        before = probe.stat().st_mtime_ns
        result = self.upgrade("0.4.0")
        self.assert_ok(result)
        self.assertIn("0.4.0 already installed", result.stdout)
        self.assertEqual(probe.stat().st_mtime_ns, before)
        self.assertEqual(self.current(), "0.4.0")

    def test_the_same_version_with_other_content_is_refused(self):
        # A release is republished under a version that is installed already.
        self.api.publish("0.4.0")
        self.assert_ok(self.upgrade("0.4.0"))
        self.api.publish("0.4.0", marker="second")
        result = self.upgrade("0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("exists with other content", result.stderr)
        probe = run([self.root / "0.4.0" / "libexec" / "jig-probe"], env=self.env())
        self.assertEqual(probe.stdout, "0.4.0 first\n")

    def test_the_pin_is_written_in_the_conf_file_of_every_role_account(self):
        # The version is installed; the orchestrator's conf file is changed here and
        # each worker's through ssh, once per account. A worker conf file with no
        # JIG_VERSION line gets one, and its other lines stay.
        self.api.publish("0.4.0")
        self.assert_ok(self.upgrade("0.4.0"))
        self.project(worker_text="REPO=demo\nBRANCH=0.1")
        result = self.upgrade("demo", "0.4.0")
        self.assert_ok(result)
        confs = self.confs()
        self.assertIn("\nJIG_VERSION=0.4.0\nORCH_ACCOUNT=", confs[ORCH])
        self.assertNotIn("0.3.0", confs[ORCH])
        for account in WORKERS:
            self.assertEqual(confs[account], "REPO=demo\nBRANCH=0.1\nJIG_VERSION=0.4.0\n")
        targets = (self.fake / "ssh-targets").read_text().split()
        # One check and one write in each worker account, and none for the orchestrator.
        self.assertEqual(sorted(targets), ["implementer@localhost"] * 2
                         + ["specwriter@localhost"] * 2)
        self.assertIn("pinned to 0.4.0 in 3 accounts", result.stdout)
        # The dispatcher of the installed version now runs the pinned tree.
        which = run([self.root / "current" / "bin" / "jig", "probe", "demo"],
                    env=self.env(JIG_ROOT=str(self.root)))
        self.assertEqual(which.stdout, "0.4.0 first\n")

    def test_a_pin_to_a_missing_version_installs_it_then_pins(self):
        self.api.publish("0.4.0")
        self.project()
        result = self.upgrade("demo", "0.4.0")
        self.assert_ok(result)
        self.assertEqual(self.current(), "0.4.0")
        for text in self.confs().values():
            self.assertIn("JIG_VERSION=0.4.0\n", text)
        self.assertLess(result.stdout.index("installed under"),
                        result.stdout.index("pinned to 0.4.0"))

    def test_a_failed_install_writes_no_pin(self):
        self.api.publish("0.4.0", served={"jig-0.4.0.tar.gz": b"not the package"})
        self.project()
        before = self.confs()
        result = self.upgrade("demo", "0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match its hash", result.stderr)
        self.assertEqual(self.confs(), before)
        self.assertFalse((self.root / "0.4.0").exists())

    def test_a_lock_of_the_project_refuses_and_changes_no_conf(self):
        # A unit of the project runs in the implementer account.
        self.api.publish("0.4.0")
        self.project()
        (self.locks / "demo-implementer").write_text("session=demo-impl-unit\n")
        before = self.confs()
        result = self.upgrade("demo", "0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("demo-implementer exists", result.stderr)
        self.assertEqual(self.confs(), before)
        self.assertFalse(self.root.exists())

    def test_a_lock_of_another_project_does_not_refuse(self):
        self.api.publish("0.4.0")
        self.project()
        (self.locks / "other-implementer").write_text("session=other-impl-unit\n")
        self.assert_ok(self.upgrade("demo", "0.4.0"))

    def test_a_missing_worker_conf_refuses_and_changes_no_conf(self):
        self.api.publish("0.4.0")
        self.project()
        self.conf_path("specwriter").unlink()
        kept = (ORCH, "implementer")
        before = {account: self.conf_path(account).read_text() for account in kept}
        result = self.upgrade("demo", "0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("account specwriter: cannot read", result.stderr)
        self.assertEqual({account: self.conf_path(account).read_text() for account in kept},
                         before)
        self.assertFalse(self.root.exists())

    def test_upgrade_runs_by_its_path_from_a_fixture_tree(self):
        # The command runs from a clone's tree, with that tree's install script, while
        # current is an older version that has no upgrade command and no usable
        # install script. The repository comes from the origin of the account's jig
        # clone, beside ~/ws/src/jig-dev, as no override names it.
        old = self.root / "0.3.0"
        (old / "tools").mkdir(parents=True)
        (old / "tools" / "install.sh").write_text("#!/bin/bash\nexit 9\n")
        (old / "tools" / "install.sh").chmod(0o755)
        (self.root / "current").symlink_to("0.3.0")
        (self.home / "ws" / "src" / "jig-dev").mkdir(parents=True)
        clone = self.home / "ws" / "src" / "jig"
        (clone / "libexec").mkdir(parents=True)
        (clone / "tools").mkdir()
        (clone / "lib").mkdir()
        shutil.copy(UPGRADE, clone / "libexec" / "jig-upgrade")
        shutil.copy(UPGRADE.parent.parent / "lib" / "jig_clones.py", clone / "lib")
        script = INSTALL.read_text().replace(
            "set -euo pipefail\n", "set -euo pipefail\necho 'install: from the clone'\n", 1)
        (clone / "tools" / "install.sh").write_text(script)
        (clone / "tools" / "install.sh").chmod(0o755)
        self.git_init(clone)
        origin = run(["git", "-C", clone, "remote", "add", "origin",
                      "git@github.invalid:fixture-owner/jig.git"], env=self.env())
        self.assert_ok(origin)
        self.api.publish("0.4.0")
        result = self.upgrade("0.4.0", tool=clone / "libexec" / "jig-upgrade",
                              JIG_UPGRADE_REPO="")
        self.assert_ok(result)
        self.assertIn("install: from the clone", result.stdout)
        self.assertIn(f"release v0.4.0 of {SLUG}", result.stdout)
        self.assertEqual(self.current(), "0.4.0")

    def test_usage_errors_exit_2(self):
        for args in ((), ("demo",), ("v0.4.0",), ("0.4",), ("0.4.0", "demo"),
                     ("demo", "0.4.0", "extra"), ("Demo", "0.4.0"), ("--force", "0.4.0")):
            with self.subTest(args=args):
                result = self.upgrade(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage: jig upgrade", result.stderr)
        self.assertEqual(self.api.requests, [])
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
