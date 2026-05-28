# Security Policy

## Scope

Stipend is a **development-time policy enforcement and audit toolkit** for AI agents. It does not move real money. It is not production financial infrastructure. The cryptographic, regulatory, and operational concerns of moving real funds belong to [AgentRail](https://agentrail.com), our paid product, which is in private beta.

That said, Stipend is payments-adjacent. Bugs that affect how policy decisions are made, how audit entries are written, or how the MCP server processes requests are taken seriously.

## Reporting a vulnerability

Email **security@agentrail.com** with the following:

- A clear description of the vulnerability.
- Steps to reproduce, ideally with a minimal proof-of-concept.
- The Stipend version and platform you observed it on.
- Whether you intend to disclose publicly, and on what timeline.

We will acknowledge receipt within **3 business days** and aim to provide a substantive update (fix, mitigation, or planned timeline) within **14 days**.

Please **do not** open public GitHub issues for security reports. Please do not attempt to exploit the vulnerability on any infrastructure you do not own.

## Supported versions

Stipend is at v0.1. The current minor series is the only supported version. Security patches will be released as patch versions on the latest minor series. Older minor series receive fixes only at the maintainer's discretion.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | yes       |
| < 0.1   | no        |

## Boundaries

Stipend explicitly **does not** provide:

- Real banking integration (ACH, Wire, RTP, FedNow, SEPA, USDC routing).
- Real KYC / OFAC / sanctions screening.
- Cryptographic signing of payment instructions.
- Multi-tenant authentication or authorization.
- Audit-log tamper-evidence (the JSONL log is plain text and the user controls the file).
- Encrypted storage of policy or audit data.

If your use case requires any of the above, please reach out about [AgentRail](https://agentrail.com) instead.

## Coordinated disclosure

We follow responsible-disclosure practice. If you find a vulnerability and report it privately, we will credit you in the release notes for the fix unless you prefer to remain anonymous.
