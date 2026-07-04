# AWS deployment guide

This app has **two independent deploy paths** for **two different GHCR packages**. Read the table below first to know which section applies to you.

| Package | GHCR image | How it is deployed | GitHub environment (this repo) |
|---------|------------|---------------------|----------------------------------|
| **breeze-core-engine** (current app) | `ghcr.io/<org>/breeze-core-engine:latest` | **CloudFormation only**, via breeze-saas-portal's Console — this is what customers actually run | `production` (image publish only; no deploy workflow) |
| **icici-breeze-modern** (legacy) | `ghcr.io/<org>/icici-breeze-modern:latest` | Legacy manual GitHub Actions workflows in this repo (dormant unless someone dispatches them) | `production` (Amit), `production-rakesh` (Rakesh) |

`ghcr-publish-main.yml` / `ghcr-publish-testing.yml` publish **only** `breeze-core-engine`. Neither builds nor pushes `icici-breeze-modern` — that image comes from a separate, older publish path outside this repo's active workflows. **This repo has no GitHub Actions workflow that deploys `breeze-core-engine` to EC2.** Runtime deployment of the current app is owned entirely by breeze-saas-portal.

---

## Current model: breeze-core-engine via breeze-saas-portal

### What gets deployed

Customer stacks are provisioned from **`infra/breeze-core-engine-stack.yaml`** in the **breeze-saas-portal** repo, published to S3 and launched by a per-license CloudFormation quick-create link that the Console generates (`LicenseKey` and `UserEmail` locked as parameter defaults). The stack provisions an EC2 instance (Amazon Linux 2023 arm64, `t4g.small` by default — not Ubuntu; Ubuntu is only the *legacy* path below), an Elastic IP, a security group, a small EBS data volume, and Lambda-backed start/stop scheduling. Full CFN detail (resources, parameters, power scheduling) lives in breeze-saas-portal's own docs, not duplicated here — see **[breeze-saas-portal: AWS deployment](../../breeze-saas-portal/docs/aws-deployment.md)** and **[breeze-saas-portal: License management](../../breeze-saas-portal/docs/license-management.md)**.

EC2 UserData writes `/opt/breeze-core-engine/.env` (a fresh `JWT_SECRET`, `PUBLIC_FRONTEND_ORIGIN` from the assigned Elastic IP, `DEPLOYMENT_LICENSE_KEY`, `PORTAL_API_BASE_URL`, `DEPLOYMENT_GHCR_IMAGE`), starts a `breeze-redis` sidecar container, then pulls and runs the `breeze-core-engine` image with `--env-file /opt/breeze-core-engine/.env -v /opt/breeze-core-engine/data:/app/backend/data`. After the Elastic IP associates, a CloudFormation custom resource registers the instance with the portal (`POST /api/public/register-deployment`).

### Public GHCR image vs runtime secrets

The published `breeze-core-engine` image is intended to be **public** on GHCR. It contains only:

- Next.js **standalone** output (no repo-root `.env` baked in — `DOCKER_BUILD=1` skips `loadEnvConfig` in `frontend/next.config.js`)
- Whitelisted Python **source** under `backend/src/` (with `compileall` bytecode), `backend/static/`, and empty SQLite / limit-file **templates**
- nginx + supervisor config under `deploy/`

It does **not** contain operator API keys, database passwords, `JWT_SECRET`, Google OAuth secrets, or any `.env` file. The root [`.dockerignore`](../.dockerignore) and multi-stage [Dockerfile](../Dockerfile) exclude tests, dev helpers, local DBs, and env files from the build context. `ghcr-publish-main.yml` does not pass application secrets to `docker/build-push-action`; after each push it scans the image for `.env*` files and common secret patterns.

**All sensitive configuration is injected at container start** on the customer EC2 instance — see "What gets deployed" above.

### Cross-repo DRM key contract

Production `breeze-core-engine` images **always** ship with the portal's DRM material baked in at `/etc/breeze/portal_heartbeat_public.pem` and `/etc/breeze/portal_allowed_hosts.txt`. `ghcr-publish-main.yml` **fails the build** if `CONSOLE_API_PUBLIC_BASE_URL` or a heartbeat public key is missing — this app cannot be published without it.

To generate a key pair, run breeze-saas-portal's `scripts/generate-portal-heartbeat-jwt-keys.sh`, then set **`PORTAL_HEARTBEAT_JWT_PRIVATE_KEY_B64`** as a secret on **both** repos: breeze-saas-portal uses it to sign `policy_token`s, and this repo's build derives the matching public key to bake into the image. An explicit **`PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PEM`** / **`_B64`** can override derivation if needed. Never point a customer's `PORTAL_API_BASE_URL` at a host that isn't on the image's baked allowlist — the heartbeat will be silently refused. Rebuild `breeze-core-engine` (push to `main` or `testing` — both rebuild the same `:latest` tag, see CI/CD artifacts in [Architecture](./architecture.md)) whenever the key pair or allowed portal hostname changes. Full licensing lifecycle, APIs, and runtime enforcement: **[breeze-saas-portal/docs/license-management.md](../../breeze-saas-portal/docs/license-management.md)**.

Making the GHCR package public only affects **pull authentication**; it does not change where secrets live.

### GitHub environments (this repo)

- **`production`** — used by `ghcr-publish-main.yml`/`ghcr-publish-testing.yml` for the DRM build-time secrets (`CONSOLE_API_PUBLIC_BASE_URL`, `PORTAL_HEARTBEAT_JWT_PRIVATE_KEY_B64`, etc.). There is no deploy job in this environment for the current app.
- **`production-breeze-core-engine`** (optional) — create this if you want operator-facing config (approval rules, documentation) isolated from the legacy environments below. It carries no deploy secrets today since deploys are CFN-only.

---

## Legacy model: manual GitHub Actions dispatch (dormant)

This describes the **legacy manual GitHub Actions workflows** [`legacy-aws-deploy-amit.yml`](../.github/workflows/legacy-aws-deploy-amit.yml) and [`legacy-aws-deploy-rakesh.yml`](../.github/workflows/legacy-aws-deploy-rakesh.yml). They are `workflow_dispatch`-only (no automatic trigger) and deploy the **older, separate** `icici-breeze-modern` package — not the current `breeze-core-engine` app. Keep reading only if you're maintaining that legacy instance; new customer deployments always go through the current model above.

It is **opinionated**: default VPC, Ubuntu **24.04 arm64**, `t4g.small`, Elastic IP association, optional **EBS** data volume. Adjust the workflow if your organisation requires a different topology (ALB, private subnets, ECS, etc.).

### What gets deployed

| Artifact | Description |
|----------|-------------|
| **Image** | `ghcr.io/<github-owner>/icici-breeze-modern:latest` (**linux/arm64**; not built by `ghcr-publish-main.yml` in this repo — comes from a separate legacy publish path). |
| **Runtime** | One `docker run` with `-p 80:3000` — host port **80** maps to **nginx** inside the container on **3000**. |
| **Data** | Optional persistent volume mounted at `/app/backend/data` inside the container (SQLite, ICICI masters, limits). |
| **Secrets** | Full `.env` written on the instance from GitHub Actions secret `APP_ENV_FILE_B64`. |

---

## Prerequisites (legacy workflows)

### 1. AWS account and permissions

The GitHub OIDC role (see below) needs permissions to:

- `ec2:RunInstances`, `ec2:TerminateInstances`, `ec2:Describe*`
- `ec2:CreateSecurityGroup`, `ec2:AuthorizeSecurityGroupIngress`
- `ec2:AllocateAddress`, `ec2:AssociateAddress`, `ec2:DisassociateAddress`, `ec2:DescribeAddresses`
- `ec2:AttachVolume`, `ec2:DescribeVolumes` (if using EBS)
- `ssm:GetParameter` (for Ubuntu AMI ID lookup)

### 2. GitHub Environments and OIDC

The workflow uses:

```yaml
permissions:
  id-token: write
  contents: read
  packages: read
```

and:

```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: ${{ secrets.AWS_ROLE_TO_ASSUME }}
```

**You must**:

1. Create an **IAM role** trusted by GitHub OIDC (`token.actions.githubusercontent.com`) for this repository.
2. Attach policies allowing the EC2/API calls above.
3. Store the role ARN in GitHub secret **`AWS_ROLE_TO_ASSUME`**.
4. Ensure the same role trust policy also allows `scheduler.amazonaws.com` (workflows create EventBridge Scheduler schedules).

The jobs use GitHub environments **`production`** (Amit legacy workflow, environment-scoped secrets below) and **`production-rakesh`** (Rakesh legacy workflow). Do not reuse these environment names or secrets for the current app.

### 3. EC2 key pair (workflow-specific)

The Amit workflow launches instances with:

```yaml
EC2_KEY_NAME: icici-breeze-modern-KP
```

Create an EC2 key pair with **exactly that name** in the target region (`ap-south-1` by default), or change `EC2_KEY_NAME` in the workflow to match your key. The Rakesh workflow currently does **not** pass `--key-name`.

### 4. GHCR read access on the instance

User-data runs:

```bash
echo "${GHCR_READ_TOKEN}" | docker login ghcr.io -u "${GHCR_USERNAME}" --password-stdin
docker pull "${IMAGE_NAME}:${IMAGE_TAG}"
```

Provide:

| Secret | Purpose |
|--------|---------|
| `GHCR_USERNAME` | GitHub username or `token` |
| `GHCR_READ_TOKEN` | PAT with `read:packages` (classic) or fine-grained token with package read |

The workflow's `permissions.packages: read` is for the **Actions runner** only; the **EC2 host** needs its **own** credentials to pull.

Legacy environment secrets (Amit workflow, environment `production`):

| Secret | Purpose |
|--------|---------|
| `AWS_ROLE_TO_ASSUME` | OIDC role for EC2 deploy |
| `GHCR_USERNAME` / `GHCR_READ_TOKEN` | Pull on the instance |
| `APP_ENV_FILE_B64` | Full `.env` with **fixed Elastic IP** in `PUBLIC_FRONTEND_ORIGIN`, `GOOGLE_OAUTH_REDIRECT_BASE_URL`, `ALLOWED_ORIGINS` |
| `EIP_ALLOCATION_ID` | Reused Elastic IP (`eipalloc-...`) |
| `EBS_DATA_VOLUME_ID` | Optional persistent data volume |

Encode legacy `.env`:

```bash
base64 -w0 .env-production   # Linux
```

### 5. Application environment file

| Secret | Format |
|--------|--------|
| `APP_ENV_FILE_B64` | Base64 encoding of the **entire** `.env` file content (same keys as local: `JWT_SECRET`, Google, `PUBLIC_FRONTEND_ORIGIN`, etc.). |

**Generate** (on macOS/Linux):

```bash
base64 -i .env | pbcopy   # or pipe to file; paste into GitHub secret
```

On Linux without `-i`:

```bash
base64 -w0 .env
```

**Production URL notes**:

- If the public URL is `http://YOUR_ELASTIC_IP`, set:
  - `PUBLIC_FRONTEND_ORIGIN=http://YOUR_ELASTIC_IP`
  - `GOOGLE_OAUTH_REDIRECT_BASE_URL=http://YOUR_ELASTIC_IP`
- Add **`http://YOUR_ELASTIC_IP/auth/google/callback`** to Google OAuth redirect URIs.
- Set ICICI redirect to **`http://YOUR_ELASTIC_IP/icici-return`**.
- Until you terminate TLS in front of the app, keep **`COOKIE_SECURE=false`** (or browsers will not send cookies over plain HTTP).

### 6. Elastic IP

The workflow **does not allocate a new EIP**.

Either:

- Set **`EIP_ALLOCATION_ID`** to your `eipalloc-...` (recommended). If that EIP is attached elsewhere, the workflow disassociates it first (brief downtime for anything using it).
- Or ensure at least one **unassociated** EIP exists in the account; the workflow picks the first free allocation.

### 7. Optional persistent data volume

| Secret | Purpose |
|--------|---------|
| `EBS_DATA_VOLUME_ID` | e.g. `vol-0abc...` — **gp3** volume in the **same Availability Zone** as the default subnet the workflow picks. |

Behaviour:

- Volume attaches as **`/dev/sdf`**.
- Cloud-init waits for **`/dev/nvme1n1`**, **`/dev/xvdf`**, or **`/dev/sdf`** (Nitro vs Xen).
- If filesystem missing, **`mkfs.ext4`** with label **`breeze-core-data`**.
- Mounted at **`/opt/breeze-core-engine/data`**, fstab entry `LABEL=breeze-core-data`.
- Docker run includes `-v /opt/breeze-core-engine/data:/app/backend/data`.

The workflow **validates AZ** matches the launch subnet before starting.

**If you omit `EBS_DATA_VOLUME_ID`**: data lives on instance root disk only—lost when the instance is replaced.

---

## Legacy workflow behaviour (step-by-step)

1. **Configure AWS credentials** via OIDC.
2. **Resolve image** `ghcr.io/<owner>/icici-breeze-modern`.
3. **Emit user-data script** that:
   - Installs Docker from Docker's apt repo.
   - Optionally mounts EBS at `/opt/breeze-core-engine/data`.
   - Writes `/opt/breeze-core-engine/.env` from `APP_ENV_FILE_B64`.
   - Logs into GHCR and `docker pull`.
   - Stops/removes old `breeze-core-engine-app` container.
   - Runs:
     `docker run -d --name breeze-core-engine-app --restart unless-stopped --env-file /opt/breeze-core-engine/.env -v /opt/breeze-core-engine/data:/app/backend/data -p 80:3000 IMAGE:TAG`
     (volume mount still created as `/opt/breeze-core-engine/data` even without EBS—ephemeral root disk.)
4. **Find default VPC** and first default subnet.
5. **Ensure security group** named `{EC2_TAG}-sg` with ingress **TCP 22** and **TCP 80** from `0.0.0.0/0` (adjust for production hardening).
6. **Resolve Ubuntu 24.04 arm64** AMI via SSM parameter.
7. **Terminate** existing instances tagged `Name=Breeze-Core-Engine-EC2` (workflow constant `EC2_TAG`) and wait for termination—ensures EIP can move and no duplicate fleet.
8. **Launch** new instance with user-data.
9. **Wait** until `running`.
10. **Attach** EBS volume if secret set.
11. **Associate** Elastic IP.
12. **Sleep 60s** for cloud-init (bootstrap is not SSH-validated in the workflow).
13. **Create EventBridge Scheduler schedules** for weekday auto start (09:00 IST) and stop (17:00 IST).
14. **Print** instance id, public IP, URL `http://<ip>`.

Configurable **env** at job level (edit workflow to change):

| Variable | Default |
|----------|---------|
| `AWS_REGION` | `ap-south-1` |
| `INSTANCE_TYPE` | `t4g.small` |
| `IMAGE_TAG` | `latest` |
| `EC2_TAG` | `Breeze-Core-Engine-EC2` |

---

## Security hardening (recommended, legacy path)

1. **Restrict SSH**: Change security group rule for port 22 from `0.0.0.0/0` to your office IP or a bastion.
2. **HTTPS**: Put **ACM + ALB** or **CloudFront** in front, or use **Caddy/nginx on host** with Let's Encrypt, then set `COOKIE_SECURE=true` and use `https://` in `PUBLIC_FRONTEND_ORIGIN`.
3. **Secrets rotation**: Rotate `JWT_SECRET` only with a plan—existing encrypted credentials need re-entry.
4. **IMDSv2** and **instance profile**: Consider replacing GHCR user/pass with an instance role + ECR pull if you migrate registries.
5. **Scheduler least privilege**: Scope scheduler, `iam:PassRole`, and EC2 start/stop permissions to the schedules/instances used by deployment.

---

## Operations (legacy path)

### View logs on the instance

SSH as `ubuntu` with your key:

```bash
ssh -i your-key.pem ubuntu@<ELASTIC_IP>
sudo docker logs -f breeze-core-engine-app
```

### Update after a new image

1. Ensure the legacy `icici-breeze-modern` publish path ran and pushed `latest` (or bump `IMAGE_TAG` in the workflow).
2. Re-run the appropriate manual deploy workflow (Amit or Rakesh) (it replaces the instance and re-pulls).

### Backup

- Snapshot the **EBS volume** if `EBS_DATA_VOLUME_ID` is used.
- Export a copy of your **`.env`** (password manager or sealed vault)—`APP_ENV_FILE_B64` is not retrievable from GitHub after the fact in plaintext.

### Health

The Dockerfile **HEALTHCHECK** requests `http://127.0.0.1:3000/health` (nginx → FastAPI), matching the breeze-saas-portal backend probe used for CFN-deployed instances too. For a manual check: **`http://<EIP>/`** (UI HTML) and **`http://<EIP>/health`** (JSON `{"status":"ok",...}`).

---

## Troubleshooting (legacy path)

| Symptom | Likely cause |
|---------|----------------|
| Pull fails on instance | Wrong `GHCR_*` secrets or image private without access. |
| `No unassociated Elastic IP` | Create EIP or set `EIP_ALLOCATION_ID`. |
| AZ mismatch error | Volume AZ ≠ subnet AZ; move volume or change subnet. |
| Google OAuth error | `GOOGLE_OAUTH_REDIRECT_BASE_URL` does not match browser URL or redirect URI missing in console. |
| ICICI loop | `PUBLIC_FRONTEND_ORIGIN` / broker redirect URL mismatch. |
| Empty data after deploy | No EBS volume; new instance has fresh SQLite. |

---

## Related documents

- [Architecture](./architecture.md) — container layout, nginx paths, and the portal-integration/reference-data subsystems.
- [Configuration reference](./configuration-reference.md) — every env var, including the `DEPLOYMENT_*`/`PORTAL_*` ones used by the current model.
- [Flows](./flows.md) — deploy flow diagrams.
- [breeze-saas-portal: License management](../../breeze-saas-portal/docs/license-management.md) — authoritative cross-repo doc for licensing, heartbeat, and CloudFormation.
- [breeze-saas-portal: AWS deployment](../../breeze-saas-portal/docs/aws-deployment.md) — CFN stack detail for the current model, plus how breeze-saas-portal itself is hosted (a separate, unrelated deployment).
