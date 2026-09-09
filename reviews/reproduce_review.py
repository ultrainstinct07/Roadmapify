"""Run the desired-behavior regressions for the 18 original review defects.

The original reproductions were converted into tests/test_review_regressions.py.
A successful run now means the regression assertions pass, not that defects
were reproduced. Requires the development test dependency (pytest).
"""
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q",
                                     "tests/test_review_regressions.py"], cwd=root))
