# Security Policy

## Supported versions

Only the latest released `0.1.x` version receives security fixes. Development snapshots and older `0.1.x` releases may be used to reproduce a report, but fixes target the latest version.

## Reporting a vulnerability

Do not open a public issue for a vulnerability, exposed credential, authentication-boundary problem, or report containing sensitive run data.

Prefer GitHub Private Vulnerability Reporting for this repository. If that is unavailable, email **xuanzening123@gmail.com** with the subject `Token Odyssey security report`.

Include the affected version or commit, impact, a minimal reproduction, and any suggested mitigation. Remove API keys, prompts, private thoughts, and unrelated run data before attaching files. You should receive an acknowledgement within seven days; disclosure timing will be coordinated after the report is validated and a mitigation is ready.

## Credential and log handling

Use `TOKEN_ODYSSEY_API_KEY` or a Git-ignored one-line `api_key_file`. Token Odyssey must never write API keys to prompts or run records. Nevertheless, `runs/` can contain full model exchanges and private story data, so treat it as sensitive and do not commit it.
