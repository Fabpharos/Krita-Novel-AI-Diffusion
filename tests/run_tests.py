"""Minimal test runner so pytest is optional: python tests/run_tests.py"""
import importlib.util
import os
import sys
import traceback

here = os.path.dirname(os.path.abspath(__file__))
failed = passed = 0
for fname in sorted(os.listdir(here)):
    if not (fname.startswith("test_") and fname.endswith(".py")):
        continue
    spec = importlib.util.spec_from_file_location(fname[:-3], os.path.join(here, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in sorted(dir(mod)):
        fn = getattr(mod, name)
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
            except Exception:
                failed += 1
                print(f"FAIL {fname}::{name}")
                traceback.print_exc()
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
