# Deploying Shadow

Shadow ships as **one container**: the FastAPI backend serves the JSON API and
the built React UI on a single port. This directory holds everything needed to
run it locally in Docker and to deploy it to a managed public HTTPS URL on AWS
**App Runner** (the lowest-friction option; ECS Fargate notes at the end).

Nothing secret is baked into the image — all credentials are read from the
environment / Secrets Manager at runtime, and `/health` is the readiness probe.

---

## 1. Build the image

```bash
# from the repo root
docker build -t shadow:latest .
```

The build is two-stage: Node builds the Vite/React UI into `web/static-react`,
then a slim Python 3.13 runtime installs the app with its `[agents]` extras and
copies the built UI in. The final image runs as a non-root user.

## 2. Run it locally

```bash
docker run --rm -p 8080:8080 \
  -e SHADOW_DEV=1 \
  -e BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20240620-v1:0 \
  -e AWS_REGION=us-east-1 \
  -e SHADOW_SESSION_SECRET=dev-secret-change-me \
  --env-file .env \
  -v "$PWD/seed/ids.json:/app/seed/ids.json:ro" \
  shadow:latest
```

- `seed/ids.json` is tenant-specific (gitignored); mount it read-only.
- `--env-file .env` supplies the Box CCG creds for live Box reads. Without them
  the app still boots and the Box-backed features degrade honestly.
- Open http://127.0.0.1:8080/health → `{"status":"ok", ...}`.

## 3. Push to ECR

```bash
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
REPO=shadow

aws ecr create-repository --repository-name "$REPO" --region "$REGION" 2>/dev/null || true
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$AWS_ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

docker tag shadow:latest "$AWS_ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest"
docker push "$AWS_ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest"
```

## 4. Create the secrets

Store the Box + Bedrock config in Secrets Manager (one JSON secret is simplest):

```bash
aws secretsmanager create-secret --name shadow/config --region "$REGION" \
  --secret-string '{
    "BOX_CLIENT_ID":"...",
    "BOX_CLIENT_SECRET":"...",
    "BOX_ENTERPRISE_ID":"...",
    "BEDROCK_MODEL_ID":"anthropic.claude-3-5-sonnet-20240620-v1:0",
    "SHADOW_SESSION_SECRET":"<a long random string>"
  }'
```

The App Runner **instance role** needs: `secretsmanager:GetSecretValue` on that
secret, `bedrock:InvokeModel` on the model, and DynamoDB
`CreateTable`/`DescribeTable`/`PutItem`/`Scan` on `shadow_activity` (the
entrypoint provisions the table on first boot). The **access role** needs ECR
pull.

## 5. Create the App Runner service

Console path (simplest): App Runner → Create service → Container registry →
your ECR image → port **8080** → health-check path **/health** → attach the
instance role → map the secret keys to env vars. See `apprunner.yaml` for the
intended runtime shape.

CLI path: `aws apprunner create-service` with an `ImageRepository` source,
`Port=8080`, a `HealthCheckConfiguration` of `Path=/health, Protocol=HTTP`, and
`InstanceConfiguration.InstanceRoleArn` set to the instance role. App Runner
returns a public `*.awsapprunner.com` URL and manages TLS + autoscaling.

The entrypoint provisions DynamoDB on first boot (idempotent) so the activity
feed uses DynamoDB, not the JSONL fallback.

---

## ECS Fargate (if you need a VPC / existing ALB)

Same image. Run it as a Fargate task behind an ALB: target group health check
`/health`, container port 8080, task role with the same permissions as the App
Runner instance role, secrets injected via the task definition's `secrets`
block (valueFrom the Secrets Manager ARN). App Runner is preferred unless you
need VPC networking or an existing ALB.
