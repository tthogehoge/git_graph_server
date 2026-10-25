import unittest

from git_graph import winproc


class WinProcTests(unittest.TestCase):
    def make_cls(self):
        calls = []

        class FakePopen:
            def __init__(self, *args, **kwargs):
                calls.append(kwargs)

        return FakePopen, calls

    def test_adds_no_window_flag_by_default(self):
        cls, calls = self.make_cls()
        self.assertTrue(winproc._wrap_popen(cls))
        cls(["git", "status"])
        self.assertEqual(calls[-1]["creationflags"], winproc.CREATE_NO_WINDOW)

    def test_keeps_explicit_creationflags(self):
        cls, calls = self.make_cls()
        winproc._wrap_popen(cls)
        cls(["editor"], creationflags=0x00000008)  # DETACHED_PROCESS (外部アプリの起動)
        self.assertEqual(calls[-1]["creationflags"], 0x00000008)

    def test_wraps_only_once(self):
        cls, calls = self.make_cls()
        self.assertTrue(winproc._wrap_popen(cls))
        self.assertFalse(winproc._wrap_popen(cls))
        cls(["git"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
