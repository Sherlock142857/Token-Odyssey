# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) for its stated public compatibility boundary.

## [Unreleased]

## [0.1.0] - 2026-09-11

### Added

- An authoritative World Harness with typed intents, `Poss` checks, atomic action/mechanics transactions, invariant validation, and per-character observation projection.
- Scripted, Human, and OpenAI-compatible LLM participants behind one contract.
- Single-Act CLI and localhost/HTML demos plus multi-Act Campaign generation, checkpoints, recovery, summaries, and character memory.
- Replayable Run Log v4 records, offline dual-path `selftest`, explicit billable `verify-live`, and connection testing.
- Interaction, weighted, and shuffled turn routing; Scenario v3 and RunConfig v3 validation.
- English and Chinese project introductions, architecture documentation, source-install instructions, community templates, security policy, and CI quality gates.

### Changed

- Reset the public package version from the internal development version `0.3.0` to the first public version `0.1.0`.
- Standardized the public demo on `scenarios/floodgate_dispatch.yaml` and moved the sealed-chalice scenario to test fixtures.
- Consolidated live verification and localhost HTTP boundary code; separated Campaign scenario validation and evidence preparation from session orchestration.
- Standardized API examples on the Git-ignored `configs/llm.local.yaml` convention and `TOKEN_ODYSSEY_API_KEY`.

### Removed

- Old compatibility fallbacks and tests for unsupported historical schemas.
- Ad-hoc live-test scripts, hard-coded model choices, tracked editor settings, and obsolete implementation/test reports.

### Compatibility

- Public v0.1 compatibility covers the CLI, Scenario v3, RunConfig v3, Run Log v4, and Campaign Checkpoint v1.
- Python module paths and localhost HTTP endpoints remain internal. Older data formats are rejected and are not migrated.

[Unreleased]: https://github.com/Sherlock142857/Token-Odyssey/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Sherlock142857/Token-Odyssey/releases/tag/v0.1.0
