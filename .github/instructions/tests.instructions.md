---
description: "Use when writing or running Python regression tests and development safety checks."
applyTo: "tests/**/*.py,scripts/**/*.py"
---
# Offline Tests

- Use existing `unittest` patterns, `unittest.mock`, and temporary files/databases. No broker credentials or authenticated calls; no operational state paths.
- Run `python scripts/offline_tests.py --pattern test_<area>.py` before the full `python scripts/offline_tests.py` suite. Verify the actual interpreter first.
- Prefer a failing regression for a concrete behavior over mocks that only assert an implementation detail. Assert intended mutations and forbidden network/accounting mutations.
- Cover boundary values, non-finite values, partial fills, repeat observations, restart, and failure injection when relevant. Control time and random seeds rather than sleep.
- Use real calendar/pandas/SQLite behavior where those semantics matter; no broker runtime exists. Mock external provider boundaries, not the entire method under test.
- Do not launch subprocess/native network clients to bypass the offline guard. All test file accesses must be temporary; never discover the real local workspace.
- Keep new tooling tests discoverable by the existing suite. Do not weaken existing guards or assertions to force green results.