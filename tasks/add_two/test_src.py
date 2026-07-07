"""The gate. Zero dependencies: exits non-zero (asserts) at baseline, 0 once add() is correct.

Run as `python3 test_src.py` with cwd == this dir, so `from src import add` picks up the
sibling src.py the executor overwrites.
"""
from src import add

assert add(2, 3) == 5, "2 + 3 should be 5"
assert add(-1, 1) == 0, "-1 + 1 should be 0"
assert add(0, 0) == 0, "0 + 0 should be 0"
print("ok")
