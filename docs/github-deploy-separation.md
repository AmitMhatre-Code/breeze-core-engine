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

---

## breeze-saas-portal repo (CFN operator)

Customer stacks: `infra/breeze-core-engine-stack.yaml` (pulls `GhcrImage`, writes `/opt/breeze-core-engine/.env` at boot).

On the **portal** host (or in `APP_ENV_FILE_B64` for saas-portal’s own `aws-deploy`), set:

| Variable | Example |
|----------|---------|
| `CONSOLE_CFN_TEMPLATE_URL` | `https://<bucket>.s3.<region>.amazonaws.com/cfn/breeze-core-engine-stack.yaml` |
| `CONSOLE_API_PUBLIC_BASE_URL` | `https://api.example.com` |
| `CONSOLE_GHCR_IMAGE_DEFAULT` | `ghcr.io/<org>/breeze-core-engine:latest` |

After template changes, run saas-portal **`aws-deploy`** with **`CFN_TEMPLATES_BUCKET`** to republish YAML.

Customers: Console → **Deploy application** → AWS form: **`GhcrPat`**, **`LicenseKey`**, **`UserEmail`**. Env vars: see saas-portal `infra/env-parameters.example.md`. After create, register `http://<StaticPublicIpv4>/auth/google/callback` and `/icici-return` in Google/ICICI if needed.

You may add a matching GitHub environment **`production-breeze-core-engine`** on the saas-portal repo for `CFN_TEMPLATES_BUCKET` and portal `APP_ENV_FILE_B64` if you split operator secrets from portal `production`.

---

## Related docs

- [AWS deployment (legacy EC2)](./aws-deployment.md)
- [Configuration reference](./configuration-reference.md)
- breeze-saas-portal `infra/env-parameters.example.md`
