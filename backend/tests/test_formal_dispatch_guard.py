"""Registration gate prerequisite: observing liveness must not kill a process."""

import subprocess
import sys

from mergeguard import guard


def test_liveness_probe_preserves_running_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert guard._pid_alive(child.pid)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=10)
    assert not guard._pid_alive(child.pid)
