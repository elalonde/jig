import datetime
import json
import os
import sys
import unittest.mock

from tests.support import ROOT, TempTestCase

sys.path.insert(0, str(ROOT / "lib"))

import jig_context  # noqa: E402

SESSION = "jig-impl-demo"
NOW = datetime.datetime.fromisoformat("2026-11-09T14:05:11-08:00")
GOOD = {"session": SESSION, "total_input_tokens": 84213, "context_window_size": 200000,
        "used_percentage": 42.1, "written": "2026-11-09T14:02:11-08:00"}


class ContextFileTest(TempTestCase):
    def write(self, data, name=SESSION):
        text = data if isinstance(data, str) else json.dumps(data)
        (self.tmp / f"{name}.json").write_text(text, encoding="utf-8")

    def read(self, session=SESSION):
        return jig_context.read_context(session, directory=self.tmp, now=NOW)

    def test_a_good_file_gives_tokens_window_and_age(self):
        # The status line wrote three minutes before now: the age says so.
        self.write(GOOD)
        context = self.read()
        self.assertEqual((context.tokens, context.window), (84213, 200000))
        self.assertEqual(context.age, datetime.timedelta(minutes=3))
        self.assertEqual(context.readout(), "84213/200000")

    def test_the_age_counts_across_time_zones(self):
        self.write(dict(GOOD, written="2026-11-09T22:04:11+00:00"))
        self.assertEqual(self.read().age, datetime.timedelta(minutes=1))

    def test_a_missing_file_gives_nothing(self):
        self.assertIsNone(self.read())

    def test_a_file_that_does_not_parse_gives_nothing(self):
        # Each of these is a file a broken or interrupted writer could leave.
        broken = ["", "{", "[1, 2]", "\xff",
                  dict(GOOD, session="jig-impl-other"),
                  dict(GOOD, total_input_tokens="84213"),
                  dict(GOOD, total_input_tokens=True),
                  dict(GOOD, total_input_tokens=-1),
                  dict(GOOD, context_window_size=0),
                  {key: value for key, value in GOOD.items() if key != "written"},
                  dict(GOOD, written="yesterday"),
                  dict(GOOD, written="2026-11-09T14:02:11")]
        for data in broken:
            with self.subTest(data=data):
                self.write(data)
                self.assertIsNone(self.read())

    def test_the_directory_can_come_from_the_environment(self):
        self.write(GOOD)
        with unittest_env(JIG_CTX_DIR=str(self.tmp)):
            self.assertEqual(jig_context.read_context(SESSION).tokens, 84213)

    def test_the_default_directory_is_the_hosts_one(self):
        with unittest_env(JIG_CTX_DIR=None):
            self.assertEqual(str(jig_context.context_dir()), "/run/jig/ctx")


def unittest_env(**values):
    """Set or remove environment variables for the length of a with block."""
    patched = dict(os.environ)
    for key, value in values.items():
        if value is None:
            patched.pop(key, None)
        else:
            patched[key] = value
    return unittest.mock.patch.dict(os.environ, patched, clear=True)
