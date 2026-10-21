# Shadow — single-container production image.
#
# Stage 1 builds the React/TS UI with Node; stage 2 is a slim Python runtime
# that installs the app and its agent/deploy extras and serves the FastAPI
# backend (which also serves the built SPA from web/static-react) via uvicorn.
#
# One container runs the whole app: API + UI on one port. Designed for AWS App
# Runner / ECS Fargate — it binds 0.0.0.0, exposes a /health probe, and reads
# all config (Box creds, Bedrock model, AWS region) from the environment /
# Secrets Manager at runtime. Nothing secret is baked into the image.

# ---- stage 1: build the UI -------------------------------------------------- #
FROM node:20-slim AS ui
WORKDIR /ui
# install deps first (layer-cached unless package manifests change)
COPY web/ui/package.json web/ui/package-lock.json ./
RUN npm ci
# build
COPY web/ui/ ./
RUN npm run build
# vite emits to ../static-react (= /static-react here, since WORKDIR is /ui).
# Normalize to /ui/dist-out so stage 2 copies from one known path.
RUN cp -r /static-react ./dist-out

# ---- stage 2: python runtime ----------------------------------------------- #
FROM python:3.13-slim AS runtime

# non-root user — never run the server as root
RUN useradd --create-home --uid 10001 shadow
WORKDIR /app

# system deps kept minimal; no build toolchain needed for the pinned wheels
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# install the app + the agents/deploy extras (Strands, boto3, bedrock-agentcore)
COPY pyproject.toml ./
COPY core/ ./core/
COPY connectors/ ./connectors/
COPY shadow_box/ ./shadow_box/
COPY agents/ ./agents/
COPY web/ ./web/
COPY config/ ./config/
COPY seed/ ./seed/
COPY lambda/ ./lambda/
COPY deploy/ ./deploy/
RUN pip install -e ".[agents]"

# bring in the built UI from stage 1
COPY --from=ui /ui/dist-out ./web/static-react

# runtime config: bind for the platform LB, serve on $PORT (App Runner sets it)
ENV SHADOW_HOST=0.0.0.0 \
    PORT=8080 \
    AWS_REGION=us-east-1
EXPOSE 8080

USER shadow

# entrypoint provisions DynamoDB (idempotent, best-effort) then serves
ENTRYPOINT ["python", "deploy/entrypoint.py"]
