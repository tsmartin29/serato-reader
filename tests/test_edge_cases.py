"""Runs every case in shared_cases.py against serato_reader, one unittest test per case."""
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import shared_cases
from adapter import R


class Temp(object):
    def __init__(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="serato_reader_test_"))
        self.n = 0

    def mk(self):
        self.n += 1
        p = self.root / ("d%d" % self.n)
        p.mkdir()
        return p

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class EdgeCases(unittest.TestCase):
    pass


def _make(fn):
    def test(self):
        t = Temp()
        try:
            fn(R, t)
        finally:
            t.close()
    return test


for _i, (_name, _fn) in enumerate(shared_cases.CASES):
    setattr(EdgeCases, "test_%03d_%s" % (_i, "".join(c if c.isalnum() else "_" for c in _name)[:70]), _make(_fn))

if __name__ == "__main__":
    unittest.main()
