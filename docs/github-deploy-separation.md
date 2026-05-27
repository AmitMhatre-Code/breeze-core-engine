# GitHub deploy separation: legacy vs CloudFormation

Two application packages use **different deploy paths**:

| Package | GHCR image | How it is deployed | GitHub environment (breeze-core-engine repo) |
|---------|------------|-------------------|-----------------------------------------------|
| **icici-breeze-modern** (legacy) | `ghcr.io/<org>/icici-breeze-modern:latest` | [`legacy-aws-deploy-amit.yml`](../.github/workflows/legacy-aws-deploy-amit.yml) / [`legacy-aws-deploy-rakesh.yml`](../.github/workflows/legacy-aws-deploy-rakesh.yml) | **`production`** (Amit), **`production-rakesh`** (Rakesh) |
| **breeze-core-engine** (current) | `ghcr.io/<org>/breeze-core-engine:latest` | **CloudFormation only** — breeze-saas-portal Console → customer AWS stack | **`production-breeze-core-engine`** (operator / CFN config; see below) |

[`ghcr-publish.yml`](../.github/workflows/ghcr-publish.yml) publishes **only** `ghcr.io/<org>/breeze-core-engine` on push to `main`. It does **not** build or push `icici-breeze-modern`.

---

## breeze-core-engine repo

### Legacy: environment `production`

Used by **Legacy Manual AWS Deployment** workflows. Secrets (environment-scoped):

| Secret | Purpose |
|--------|---------|
| `AWS_ROLE_TO_ASSUME` | OIDC role for EC2 deploy |
| `GHCR_USERNAME` / `GHCR_READ_TOKEN` | Pull on the instance |
| `APP_ENV_FILE_B64` | Full `.env` with **fixed Elastic IP** in `PUBLIC_FRONTEND_ORIGIN`, `GOOGLE_OAUTH_REDIRECT_BASE_URL`, `ALLOWED_ORIGINS` |
| `EIP_ALLOCATION_ID` | Reused Elastic IP (`eipalloc-...`) |
| `EBS_DATA_VOLUME_ID` | Optional persistent data volume |

Legacy EC2 runs the **icici-breeze-modern** container image (`ghcr.io/<org>/icici-breeze-modern:latest`). That image is produced by your **legacy** publish pipeline (separate from `ghcr-publish.yml` in this repo). Do **not** migrate these secrets to another environment name.

Encode legacy `.env`:

```bash
base64 -w0 .env-production   # Linux
```

### New app: environment `production-breeze-core-engine`

Create **Settings → Environments → `production-breeze-core-engine`** for operator configuration tied to the **new** package. This repo has **no** GitHub Actions workflow that deploys breeze-core-engine to EC2; customer deploys use CFN only.

Use this environment for secrets you want isolated from legacy, for example:

| Secret / config | Purpose |
|-----------------|--------|
| *(optional)* `GHCR_READ_TOKEN` | If you add automation that verifies the `breeze-core-engine` image |
| Documentation / approval rules | Gate changes that affect the CFN image tag |

**Image publish:** `ghcr-publish.yml` runs on `main` without an environment block; the image is `ghcr.io/<org>/breeze-core-engine:latest`.

**Runtime deploy:** only via breeze-saas-portal — see below.

### Public GHCR image vs runtime secrets

The published image is intended to be **public** on GHCR. It contains only:

- Next.js **standalone** output (no repo-root `.env` at build time; `DOCKER_BUILD=1` skips `loadEnvConfig` in `frontend/next.config.js`)
- Whitelisted Python **source** under `backend/src/` (with `compileall` bytecode), `backend/static/`, and empty SQLite / limit-file **templates**
- nginx + supervisor config under `deploy/`

It does **not** contain operator API keys, database passwords, `JWT_SECRET`, Google OAuth secrets, or any `.env` file. The root [`.dockerignore`](../.dockerignore) and multi-stage [Dockerfile](../Dockerfile) exclude tests, dev helpers, local DBs, and env files from the build context. `ghcr-publish.yml` does not pass application secrets to `docker/build-push-action`; after each push it scans the image for `.env*` files and common secret patterns.

**All sensitive configuration is injected at container start** on the customer EC2 instance:

1. CloudFormation UserData writes `/opt/breeze-core-engine/.env` (e.g. `JWT_SECRET` from `openssl`, `PUBLIC_FRONTEND_ORIGIN` from the Elastic IP).
2. `docker run ... --env-file /opt/breeze-core-engine/.env` passes those variables into the container.

Operators add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and ICICI-related settings to that file on the instance (or extend the stack later). See breeze-saas-portal `infra/env-parameters.example.md`.

**Deployment license DRM:** When `PORTAL_API_BASE_URL` is set on the instance (from CloudFormation `RegistrationEndpoint`), the engine requires signed `policy_token` JWTs on heartbeat/login responses and only trusts the portal hostname baked into the image (`/etc/breeze/portal_allowed_hosts.txt`). Generate a key pair with breeze-saas-portal `scripts/generate-portal-heartbeat-jwt-keys.sh`; set **`PORTAL_HEARTBEAT_JWT_PRIVATE_KEY`** on the portal and **`PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PEM`** as a GitHub secret for `ghcr-publish.yml` (same org/vars as `CONSOLE_API_PUBLIC_BASE_URL`). Do not change `PORTAL_API_BASE_URL` on customer instances to a host that is not on that allowlist.

Making the GHCR package public only affects **pull authentication**; it does not change where secrets live.

---

## breeze-saas-portal repo (CFN operator)

Customer stacks: `infra/breeze-core-engine-stack.yaml` (pulls `GhcrImage`, writes `/opt/breeze-core-engine/.env` at boot).

On the **portal** host (or in `APP_ENV_FILE_B64` for saas-portal’s own `aws-deploy`), set:

| Variable | Example |
|----------|---------|
| `CONSOLE_CFN_TEMPLATE_URL` | `https://<bucket>.s3.<region>.amazonaws.com/cfn/breeze-core-engine-stack.yaml` |
| `CONSOLE_API_PUBLIC_BASE_URL` | `https://api.example.com` |
| `CONSOLE_GHCR_IMAGE_DEFAULT` | `ghcr.io/<org>/breeze-core-engine:latest` |
| `PORTAL_HEARTBEAT_JWT_PRIVATE_KEY_B64` | Base64 ES256 private PEM on portal (`aws-deploy` secret) |
| `PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PEM` or `_B64` | Matching public key baked into engine images (`ghcr-publish.yml` secrets) |

After template changes, run saas-portal **`aws-deploy`** with **`CFN_TEMPLATES_BUCKET`** to republish YAML. Rebuild **breeze-core-engine** on `main` whenever the public key or allowed portal hostname changes.

Customers: Console → **Deploy application** → AWS form: **`GhcrPat`**, **`LicenseKey`**, **`UserEmail`**. Env vars: see saas-portal `infra/env-parameters.example.md`. After create, register `http://<StaticPublicIpv4>/auth/google/callback` and `/icici-return` in Google/ICICI if needed.

You may add a matching GitHub environment **`production-breeze-core-engine`** on the saas-portal repo for `CFN_TEMPLATES_BUCKET` and portal `APP_ENV_FILE_B64` if you split operator secrets from portal `production`.

---

## Related docs

- [AWS deployment (legacy EC2)](./aws-deployment.md)
- [Configuration reference](./configuration-reference.md)
- breeze-saas-portal `infra/env-parameters.example.md`
