# AWS deployment guide

This guide describes the **manual GitHub Actions workflows** [`.github/workflows/aws-deploy-amit.yml`](../.github/workflows/aws-deploy-amit.yml) and [`.github/workflows/aws-deploy-rakesh.yml`](../.github/workflows/aws-deploy-rakesh.yml) that provision an **EC2** instance, install **Docker**, pull the **GHCR** image from [`.github/workflows/ghcr-publish.yml`](../.github/workflows/ghcr-publish.yml), and run the **all-in-one** container from the root [`Dockerfile`](../Dockerfile).

It is **opinionated**: default VPC, Ubuntu **24.04 arm64**, `t4g.small`, Elastic IP association, optional **EBS** data volume. Adjust the workflow if your organisation requires a different topology (ALB, private subnets, ECS, etc.).

---

## What gets deployed

| Artifact | Description |
|----------|-------------|
| **Image** | `ghcr.io/<github-owner>/<repo-lowercase>:latest` (currently built as **linux/arm64**). |
| **Runtime** | One `docker run` with `-p 80:3000` — host port **80** maps to **nginx** inside the container on **3000**. |
| **Data** | Optional persistent volume mounted at `/app/backend/data` inside the container (SQLite, ICICI masters, limits). |
| **Secrets** | Full `.env` written on the instance from GitHub Actions secret `APP_ENV_FILE_B64`. |

---

## Prerequisites

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

The jobs use GitHub environments `production` (Amit workflow) and `production-rakesh` (Rakesh workflow), so you can add protection rules and environment-scoped secrets.

### 3. EC2 key pair (workflow-specific)

The Amit workflow launches instances with:

```yaml
EC2_KEY_NAME: Breeze-Core-Engine-KP
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

The workflow’s `permissions.packages: read` is for the **Actions runner** only; the **EC2 host** needs its **own** credentials to pull.

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

## Workflow behaviour (step-by-step)

1. **Configure AWS credentials** via OIDC.
2. **Resolve image** `ghcr.io/<owner>/<repo-lower>`.
3. **Emit user-data script** that:
   - Installs Docker from Docker’s apt repo.
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

## Security hardening (recommended)

1. **Restrict SSH**: Change security group rule for port 22 from `0.0.0.0/0` to your office IP or a bastion.
2. **HTTPS**: Put **ACM + ALB** or **CloudFront** in front, or use **Caddy/nginx on host** with Let’s Encrypt, then set `COOKIE_SECURE=true` and use `https://` in `PUBLIC_FRONTEND_ORIGIN`.
3. **Secrets rotation**: Rotate `JWT_SECRET` only with a plan—existing encrypted credentials need re-entry.
4. **IMDSv2** and **instance profile**: Consider replacing GHCR user/pass with an instance role + ECR pull if you migrate registries.
5. **Scheduler least privilege**: Scope scheduler, `iam:PassRole`, and EC2 start/stop permissions to the schedules/instances used by deployment.

---

## Operations

### View logs on the instance

SSH as `ubuntu` with your key:

```bash
ssh -i your-key.pem ubuntu@<ELASTIC_IP>
sudo docker logs -f breeze-core-engine-app
```

### Update after a new image

1. Ensure **`ghcr-publish`** ran and pushed `latest` (or bump `IMAGE_TAG` in the workflow).
2. Re-run the appropriate manual deploy workflow (Amit or Rakesh) (it replaces the instance and re-pulls).

### Backup

- Snapshot the **EBS volume** if `EBS_DATA_VOLUME_ID` is used.
- Export a copy of your **`.env`** (password manager or sealed vault)—`APP_ENV_FILE_B64` is not retrievable from GitHub after the fact in plaintext.

### Health

The Dockerfile **HEALTHCHECK** requests `http://127.0.0.1:3000/` (nginx → Next). The FastAPI **`/health`** route is **not** listed in `deploy/nginx.all-in-one.conf`, so **`http://<EIP>/health`** from the internet may not reach the API unless you add a `location` block (mirror `nginx.conf` in the repo) or terminate checks inside the container. For a quick external check, use **`http://<EIP>/`** (expect HTML from the UI).

---

## Troubleshooting

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

- [Architecture](./architecture.md) — container layout and nginx paths.
- [Configuration reference](./configuration-reference.md) — every env var.
- [Flows](./flows.md) — deploy flow diagram (section 16).
