03_TESTS — TESTS AND BUILD TOOLS
=================================

The source and install package live in 04_SOURCE. Tests do not install HKS or
change system services. Run a Python test from the workspace root with:

  py 03_TESTS/test_logic.py

For the browser UI smoke test, install its declared dependency once, then run:

  npm ci --prefix 03_TESTS
  node 04_SOURCE/HKS_AdminPanel/tests/ui_smoke.js

Key tools:
  build_zip.py       validates source and builds the zip and split delivery files
  e2e_test.py        live HTTP/WebSocket flow with mock agents
  mock_agent.py      test-only simulated agent; it does not control a real PC

The build validator writes deliverables. Review its output paths before running
it if you need to preserve an existing zip or delivery set.
