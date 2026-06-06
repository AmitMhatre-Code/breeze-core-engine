# Copyright submission guide

This guide prepares a repository submission package for the Copyright Office of India for Breeze Modern.

## Scope statement

The submission package includes first-party original expression in this repository, including:

- Backend application source under `backend/src/icici_breeze_backend/`
- Backend tests under `backend/tests/`
- Frontend source under `frontend/src/`
- First-party public assets under `frontend/public/`
- Deployment and CI definitions under `deploy/` and `.github/workflows/`
- Root authored infrastructure/config files (`Dockerfile`, `docker-compose.yml`, `dev.sh`, `nginx.conf`, `README.md`)
- Technical documentation under `docs/`

The submission package excludes third-party/common libraries, generated files, runtime artifacts, secrets, and local data stores. See [Third-party exclusions](./third-party-exclusions.md).

## Originality and ownership template

Use/adapt this statement in your filing:

1. The author/applicant claims copyright in the original source code, structure, module organization, and technical documentation of Breeze Modern.
2. The work includes integrations with external services and open-source components, but no ownership is claimed over third-party libraries, SDKs, platforms, trademarks, or APIs.
3. The submitted code bundle excludes external/common libraries and generated/runtime outputs; only first-party authored material is included.

## Filing checklist

1. Generate the latest consolidated code document: `docs/code-submission.md`.
2. Verify exclusions from [Third-party exclusions](./third-party-exclusions.md) are not present in the generated document.
3. Export `docs/code-submission.md` to PDF for filing (single consolidated document).
4. Include this guide and other supporting docs if filing requires explanatory material.
5. Confirm no secrets/credentials are present in any submitted file.

## Notes for legal packaging

- Keep a copy of the exact git commit used for generation.
- Keep this package synchronized with future feature releases by regenerating `docs/code-submission.md`.
- `legacy/` is a read-only historical snapshot and should not be part of the active application copyright submission.
