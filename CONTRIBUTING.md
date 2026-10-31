# Contributing

Thanks for helping. The plugin is small on purpose: the simplest change that works wins.

1. **Open an issue first** for anything bigger than a fix, so we agree on the problem before code.
2. **Test first.** Write a failing test, watch it fail, make it pass. `python check.py` must end with `gate: PASS`
   (tests, 100 % line coverage by `linecov.py`, no copy drift). A new guard branch needs a new row in
   `test_matrix.py` or a test next to it.
   Open the pull request as a **draft**: CI then runs only the fast static checks. Mark it *ready for review* when you
   want to merge, that starts the slow full suite against Hermes main.
3. You need a Hermes checkout/venv: the tests use the real `kanban_db`. Run `python check.py` with that Python.
4. Keep it stdlib only. No new dependency for what a few lines do.
5. Update `README.md` and add a `CHANGELOG.md` entry. Bump `plugin.yaml` `version` only in a release PR.
6. Never put real hosts, tokens, profile names or paths in code, tests or docs.
7. Open PRs as drafts first; mark ready when CI is green.

Security issues: see [SECURITY.md](SECURITY.md), not a public issue.
