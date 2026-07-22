# Breeze Modern — documentation

This folder contains detailed documentation for the application. The repository [README](../README.md) stays short: high-level product description, how to run locally, and essential prerequisites (redirects and secrets). Everything below goes deeper. Start here with the index table, or open any page directly.

| Document | What it covers |
|----------|----------------|
| [Functionality](./functionality.md) | Feature areas, screens, APIs used by the UI, and behaviour at a user level. |
| [Architecture](./architecture.md) | Technical stack, runtime topology, components, data stores, and integration boundaries. |
| [Design decisions](./design-decisions.md) | Rationale for major choices (single origin, proxy model, auth, persistence, deployment shape). |
| [User and system flows](./flows.md) | End-to-end flows with sequence and flow diagrams (login, broker auth, registration, trading data, settings, deployment paths). |
| [License management](../../breeze-saas-portal/docs/license-management.md) | Deployment licensing (authoritative doc in **breeze-saas-portal**): portal APIs, trial policy, heartbeat DRM, and core-engine enforcement. |
| [Configuration reference](./configuration-reference.md) | Environment variables, defaults, Google/ICICI redirect checklist, Docker and Next env wiring. |
| [AWS deployment](./aws-deployment.md) | How this app actually gets deployed (breeze-saas-portal CloudFormation) vs the dormant legacy GitHub Actions path, GHCR image publishing, and the cross-repo DRM key contract. |

## Copyright submission package

Use the following set when preparing a filing package for the Copyright Office:

| Document | Purpose |
|----------|---------|
| [Copyright submission guide](./copyright-submission.md) | Scope, originality statement template, and filing checklist. |
| [Third-party exclusions](./third-party-exclusions.md) | Explicit denylist of external/common/generated/sensitive artifacts to omit from the filing. |
| [Single code document](./code-submission.md) | Consolidated first-party code document (PDF-ready markdown). |

The code document intentionally excludes third-party dependencies (`node_modules`, `.venv`), generated/build outputs, runtime logs, secrets (`.env*`), local databases, and the read-only `legacy/` snapshot.

The `legacy/` directory in the repo is a **read-only** historical snapshot; it is not described as part of the running system here.
