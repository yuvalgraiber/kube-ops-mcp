# Contributing

Thanks for taking a look. Bug reports, ideas and pull requests are all welcome.

## Getting set up

```bash
make install     # dependencies from uv.lock
make check       # ruff, mypy, helm lint and the tests
make demo-up     # optional: a kind cluster with broken apps to try changes against
```

The tests don't need a cluster, and CI runs the same `make check` steps plus an end-to-end test on kind.

## Pull requests

- Keep each PR to one change, with a test that would have caught the bug or covers the feature.
- Use [conventional commit](https://www.conventionalcommits.org/) messages (`feat:`, `fix:`, `docs:` and so on).
- If you add a tool, write its docstring for the model: what it returns and when to use it. That text is how the client decides which tool to call.
- Anything that widens what the server can do in a cluster (new RBAC rules, new write actions) needs a note in the PR about why it's safe.

Security problems shouldn't go in a public issue. See [SECURITY.md](SECURITY.md).
