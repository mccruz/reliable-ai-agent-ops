# Contributing

Thanks for helping improve Reliable AI Agent Ops.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --no-deps .
python -m unittest discover -s tests -v
```

## Contribution boundaries

- Use fictional services, hosts, paths, state, and receipts.
- Never include credentials, real notifications, production logs, private data, or private repository history.
- Keep examples offline and non-privileged by default.
- Add failure-path tests for security or reliability changes.
- Preserve fail-closed behavior and the human-review gate.
- Explain any new external dependency before adding it.

Please open a focused pull request with the behavior change, test evidence, and any security tradeoff.
