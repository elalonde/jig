"""The host list reader and the ssh check that a host can be reached.

Each test writes its own host list into a private directory, so no test reads the
host's real file, and the ssh checks run a stand-in for ssh that only records how it
was called.
"""

import os
import sys
import time
import unittest.mock

from tests.support import ROOT, TempTestCase

sys.path.insert(0, str(ROOT / "lib"))

import jig_hosts  # noqa: E402

# A list with one host on the orchestrators' machine and one elsewhere.
TWO_HOSTS = """\
[[host]]
name = "builder"
address = "builder.local"
local = true

[[host]]
name = "relay"
address = "192.0.2.7"
local = false
"""


class HostListTest(TempTestCase):
    def host_list(self, text, name="hosts.toml"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def assert_refused(self, path, message, name="builder"):
        with self.assertRaises(jig_hosts.HostError) as caught:
            jig_hosts.host_address(name, path)
        self.assertEqual(str(caught.exception), message)

    def test_a_named_remote_host_gives_its_address(self):
        path = self.host_list(TWO_HOSTS)
        self.assertEqual(jig_hosts.host_address("relay", path), "192.0.2.7")

    def test_a_local_host_gives_its_dot_local_name(self):
        path = self.host_list(TWO_HOSTS)
        self.assertEqual(jig_hosts.host_address("builder", path), "builder.local")

    def test_a_name_that_is_not_in_the_list_is_refused_with_the_file_named(self):
        path = self.host_list(TWO_HOSTS)
        self.assert_refused(path, f"no host printer in {path}", name="printer")

    def test_a_file_that_is_not_toml_is_refused(self):
        path = self.host_list('[[host]]\nname = "builder\n')
        with self.assertRaises(jig_hosts.HostError) as caught:
            jig_hosts.host_address("builder", path)
        self.assertTrue(str(caught.exception).startswith(
            f"host list {path} does not parse: "), str(caught.exception))

    def test_a_table_without_one_of_the_three_keys_is_refused(self):
        path = self.host_list(TWO_HOSTS.replace('address = "192.0.2.7"\n', ""))
        self.assert_refused(path, f"host list {path} is not valid: host 2 has no key address")

    def test_a_key_of_the_wrong_type_is_refused(self):
        cases = (
            (TWO_HOSTS.replace("local = false", 'local = "no"'),
             "host 2: local is not true or false"),
            (TWO_HOSTS.replace('name = "relay"', "name = 7"), "host 2: name is not a string"),
            (TWO_HOSTS.replace('address = "192.0.2.7"', "address = [192, 0, 2, 7]"),
             "host 2: address is not a string"),
            (TWO_HOSTS.replace('address = "192.0.2.7"', 'address = ""'),
             "host 2: address is empty"),
            (TWO_HOSTS.replace('address = "192.0.2.7"', 'address = "-oProxyCommand=x"'),
             "host 2: address starts with -"),
        )
        for text, detail in cases:
            with self.subTest(detail=detail):
                path = self.host_list(text)
                self.assert_refused(path, f"host list {path} is not valid: {detail}")

    def test_host_that_is_not_an_array_of_tables_is_refused(self):
        for text in ('host = "builder"\n', "host = [1, 2]\n",
                     '[host]\nname = "builder"\naddress = "builder.local"\nlocal = true\n'):
            with self.subTest(text=text):
                path = self.host_list(text)
                self.assert_refused(
                    path, f'host list {path} is not valid: "host" is not an array of tables')

    def test_two_tables_with_the_same_name_are_refused(self):
        path = self.host_list(TWO_HOSTS.replace('name = "relay"', 'name = "builder"'))
        self.assert_refused(path, f"host list {path} is not valid: "
                                  "host name builder appears twice")

    def test_a_local_host_whose_address_is_not_a_dot_local_name_is_refused(self):
        path = self.host_list(TWO_HOSTS.replace('"builder.local"', '"192.0.2.9"'))
        self.assert_refused(path, f"host list {path} is not valid: host builder is local, "
                                  "but its address 192.0.2.9 does not end in .local")

    def test_a_missing_file_is_refused_with_the_file_named(self):
        path = self.tmp / "absent.toml"
        self.assert_refused(path, f"host list {path} does not exist")

    def test_without_a_file_the_reader_uses_the_hosts_own_list(self):
        self.assertEqual(jig_hosts.HOSTS_FILE, "/srv/jig/hosts.toml")
        path = self.host_list(TWO_HOSTS)
        with unittest.mock.patch.object(jig_hosts, "HOSTS_FILE", str(path)):
            self.assertEqual(jig_hosts.host_address("relay"), "192.0.2.7")


class ReachTest(TempTestCase):
    """Each test puts a stand-in for ssh first on PATH, or names it in JIG_SSH."""

    def setUp(self):
        super().setUp()
        self.hosts = self.tmp / "hosts.toml"
        self.hosts.write_text(TWO_HOSTS, encoding="utf-8")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.args = self.tmp / "ssh-args"

    def ssh_double(self, body, name="ssh"):
        """Write a stand-in for ssh that logs its arguments, one per line, then runs body."""
        path = self.bin / name
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{self.args}'\n{body}\n",
                        encoding="utf-8")
        path.chmod(0o755)
        return path

    def environment(self, **values):
        """Run with the stand-in first on PATH and with JIG_SSH only when given."""
        patched = {key: value for key, value in os.environ.items() if key != "JIG_SSH"}
        patched["PATH"] = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        patched.update(values)
        return unittest.mock.patch.dict(os.environ, patched, clear=True)

    def logged_args(self):
        return self.args.read_text(encoding="utf-8").splitlines()

    def test_a_reachable_host_runs_ssh_in_batch_mode_with_the_ten_second_limit(self):
        self.ssh_double("exit 0")
        with self.environment():
            self.assertEqual(jig_hosts.reach("relay", self.hosts), "192.0.2.7")
        self.assertEqual(jig_hosts.REACH_SECONDS, 10)
        self.assertEqual(self.logged_args(), ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                              "192.0.2.7", "true"])

    def test_jig_ssh_names_the_ssh_command_and_keeps_its_own_words(self):
        double = self.ssh_double("exit 0", name="fake-ssh")
        with self.environment(JIG_SSH=f"{double} -p 2222"):
            self.assertEqual(jig_hosts.reach("builder", self.hosts), "builder.local")
        self.assertEqual(self.logged_args(), ["-p", "2222", "-o", "BatchMode=yes",
                                              "-o", "ConnectTimeout=10", "builder.local",
                                              "true"])

    def test_a_failed_ssh_is_refused_with_the_host_and_its_address(self):
        self.ssh_double("exit 255")
        with self.environment(), self.assertRaises(jig_hosts.HostError) as caught:
            jig_hosts.reach("relay", self.hosts)
        self.assertEqual(str(caught.exception), "cannot reach host relay at 192.0.2.7")

    def test_an_ssh_that_hangs_is_stopped_at_the_limit_and_refused(self):
        self.ssh_double("exec sleep 30")
        started = time.monotonic()
        with self.environment(), self.assertRaises(jig_hosts.HostError) as caught:
            jig_hosts.reach("relay", self.hosts, limit=1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(str(caught.exception), "cannot reach host relay at 192.0.2.7")
        self.assertIn("ConnectTimeout=1", self.logged_args())

    def test_a_host_that_is_not_in_the_list_is_refused_before_ssh_runs(self):
        self.ssh_double("exit 0")
        with self.environment(), self.assertRaises(jig_hosts.HostError) as caught:
            jig_hosts.reach("printer", self.hosts)
        self.assertEqual(str(caught.exception), f"no host printer in {self.hosts}")
        self.assertFalse(self.args.exists())


if __name__ == "__main__":
    unittest.main()
