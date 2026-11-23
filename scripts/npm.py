"""Run an npm command inside ``frontend/``.

npm is a ``.cmd`` shim on Windows, which ``subprocess`` cannot launch by bare name, so the
task runner calls this script instead of ``npm`` directly.

Usage: ``python scripts/npm.py run lint``.
"""

import shutil
import subprocess
import sys
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


def main() -> int:
    npm = shutil.which("npm")
    if npm is None:
        print("npm was not found on PATH. Install Node.js 22+.", file=sys.stderr)
        return 127
    return subprocess.call([npm, *sys.argv[1:]], cwd=FRONTEND_DIR)  # noqa: S603


if __name__ == "__main__":
    sys.exit(main())
