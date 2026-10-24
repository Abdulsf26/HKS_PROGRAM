# Contributing

1. Keep changes scoped to the product or its engineering infrastructure.
2. Do not commit credentials, personal captures, local databases/logs, TLS private keys, generated installers, or machine-specific paths.
3. Preserve Windows compatibility for installation and agent features. Document any required elevation and failure recovery.
4. Add or update focused tests for behavior changes. Run the AdminPanel tests and UI smoke test before proposing a change.
5. State what was tested on Windows hardware separately from simulated or source-level checks; do not claim untested hardware or add-on flows work.
6. Do not weaken authentication, authorization, network protections, or secure-desktop safeguards to make a test pass.
