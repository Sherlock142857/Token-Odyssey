# Contributing to Token Odyssey

Thank you for helping improve Token Odyssey. v0.1 is intentionally focused on stabilizing the World Harness, its CLI, and current data formats.

## Development setup

Work from the repository root with Python 3.12 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
token-odyssey selftest
```

Create a topic branch from `main`; use a short name that describes the change. Keep commits focused and do not rewrite shared history.

## Issues

Search existing issues before opening one. Use the Bug Report or Feature Request form and provide a minimal Scenario or reproduction when possible. Please use the private process in [SECURITY.md](SECURITY.md) for vulnerabilities, credential exposure, or other security-sensitive reports.

## Required checks

Run the complete local gate before submitting a pull request:

```bash
ruff check .
ruff format --check .
mypy src
pytest --cov=token_odyssey --cov-report=term-missing
token-odyssey selftest
```

Coverage must remain at or above 85%. Tests must be deterministic and offline; real model calls never belong in CI. Add or update tests when changing stable CLI behavior, Scenario v3, RunConfig v3, Run Log v4, Campaign Checkpoint v1, or a World Harness invariant.

## Pull requests

A pull request must explain:

- what changed and why;
- how the change was tested;
- whether it affects the stable CLI or schema compatibility boundary;
- any documentation, privacy, security, or API-cost impact.

Keep public functions and core contracts fully typed. Add responsibility-oriented docstrings where ownership, authorization boundaries, or commit timing would otherwise be unclear. Do not mix unrelated cleanup with a behavioral change.

Never commit a real API key, a local `*.local.yaml` configuration, `api.txt`, or a `runs/` directory. Run records may contain prompts, replies, private character thoughts, observations, token usage, and user-authored content.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Contributions are accepted under the repository's [AGPL-3.0-only license](LICENSE).
