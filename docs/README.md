# ICICI Breeze Modern — documentation

This folder contains detailed documentation for the application. The repository [README](../README.md) stays short: high-level product description, how to run locally, and essential prerequisites (redirects and secrets). Everything below goes deeper. Start here with the index table, or open any page directly.

| Document | What it covers |
|----------|----------------|
| [Functionality](./functionality.md) | Feature areas, screens, APIs used by the UI, and behaviour at a user level. |
| [Architecture](./architecture.md) | Technical stack, runtime topology, components, data stores, and integration boundaries. |
| [Design decisions](./design-decisions.md) | Rationale for major choices (single origin, proxy model, auth, persistence, deployment shape). |
| [User and system flows](./flows.md) | End-to-end flows with sequence and flow diagrams (login, broker auth, registration, trading data, settings, deployment paths). |
| [Configuration reference](./configuration-reference.md) | Environment variables, defaults, Google/ICICI redirect checklist, Docker and Next env wiring. |
| [AWS deployment](./aws-deployment.md) | GitHub Actions workflow, AWS resources, secrets, first-time setup, and operations. |

The `legacy/` directory in the repo is a **read-only** historical snapshot; it is not described as part of the running system here.
