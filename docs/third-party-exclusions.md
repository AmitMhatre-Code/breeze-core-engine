# Third-party and non-submission exclusions

This document defines what must be excluded from the copyright submission code package.

## Exclude: external/common dependencies

- `frontend/node_modules/**`
- `backend/.venv/**`
- Any package-manager cache, lock cache, or downloaded binary artifacts

Reason: these are third-party/common libraries and not first-party authored expression.

## Exclude: generated/build/cache outputs

- `frontend/.next/**`
- `**/__pycache__/**`
- `**/.pytest_cache/**`
- `frontend/tsconfig.tsbuildinfo`

Reason: generated artifacts do not represent original authored source.

## Exclude: runtime logs and mutable runtime state

- `logs/**`
- `backend/logs/**`
- `frontend/logs/**`

Reason: runtime output is deployment-specific and not part of authored source code.

## Exclude: secrets, credentials, and sensitive data

- `.env*` (except sanitized templates such as `.env.example`, if needed)
- `backend/static/Sample Data Files/creds.json`
- Any key/token dumps or credential snapshots

Reason: sensitive material must never be disclosed in the filing package.

## Exclude: local database/data artifacts

- `**/*.sqlite3`
- DB files under `backend/data/` (including filled and empty runtime DB artifacts)

Reason: data files are runtime state; submission should focus on source expression.

## Exclude: historical snapshot not part of active system

- `legacy/**`

Reason: repository policy treats `legacy/` as reference-only historical content.

## Include examples (first-party)

- `backend/src/icici_breeze_backend/**`
- `backend/tests/**`
- `frontend/src/**`
- `frontend/public/**` (text/code assets)
- `deploy/**`
- `.github/workflows/**`
- selected root files: `README.md`, `Dockerfile`, `docker-compose.yml`, `dev.sh`, `nginx.conf`

Use these rules when generating [Single code document](./code-submission.md).
