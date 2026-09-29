import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

FIXTURES = os.path.join(HERE, "fixtures", "claude_projects")
DEMO_ROOT = "/work/demo"


class TempEnvTestCase(unittest.TestCase):
    """Isolates state dir, Claude config dir and a writable project root."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pegada-test-")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)
        self._env = {k: os.environ.get(k) for k in ("PEGADA_STATE_DIR", "CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "PEGADA_COEFFICIENTS")}
        os.environ["PEGADA_STATE_DIR"] = os.path.join(self.tmp, "state")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.tmp, "claude")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.environ.pop("PEGADA_COEFFICIENTS", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fixture(self, rel):
        return os.path.join(FIXTURES, rel)
