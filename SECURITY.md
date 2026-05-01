# Security Policy

## Supported Versions

Moldy is in PoC. Security fixes target the latest `main` branch only.

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a Vulnerability

**Do not** open a public GitHub Issue for security problems.

Report through one of:

1. **GitHub Security Advisories** —
   <https://github.com/YooSuhwa/natural-mold/security/advisories/new>
2. **Email** — open a private channel with the maintainers (see the repo
   profile / CODEOWNERS).

When reporting, please include:

- Vulnerability class (XSS, SQLi, IDOR, prompt injection, secret leakage, etc.)
- Repro steps and minimal proof-of-concept
- Affected versions / commit SHA
- Suggested fix or mitigation if you have one

We aim to:

- Acknowledge receipt within **48 hours**
- Provide an initial assessment within **7 days**
- Publish an advisory once a fix is released

## Operational Hardening (deployer-facing)

Moldy delegates LLM calls and stores user-provided credentials, so when you
deploy a fork please verify:

1. **Secrets** — never hardcode API keys; load from env vars or HashiCorp Vault
   (`hvac` integration).
2. **Encryption at rest** — Cipher V2 (Fernet + HKDF-SHA256) is used for
   credential blobs. Rotate `ENCRYPTION_KEY` and surface key rotation through
   `credentials.key_id`.
3. **Transport** — terminate HTTPS at the edge (nginx / cloud LB). The
   FastAPI app does not redirect HTTP→HTTPS by itself.
4. **Database** — enforce strong Postgres credentials and restrict network
   access. Backups should be encrypted.
5. **Mock auth** — the bundled `get_current_user` returns a fixed mock user.
   Replace with a real auth provider before exposing publicly.
6. **Public share links** — `/api/shares/{token}` is unauthenticated by
   design; consider rate-limiting (slowapi) before exposing externally.
7. **Dependencies** — keep `langchain`, `langgraph`, `deepagents`, MCP
   adapters and the Python/Node toolchain up to date.

## Acknowledgments

Thank you to everyone who reports vulnerabilities responsibly.
