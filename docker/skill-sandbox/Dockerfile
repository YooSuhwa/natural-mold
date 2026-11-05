# The image every skill sandbox starts from (SKILL_SANDBOX_IMAGE,
# docs/design-docs/skill-sandbox-docker.md). Same runtimes as the backend image so
# a skill behaves the same in the sandbox: Python 3.12, Node 22 and the
# skill-node packages (docx, pptxgenjs, xlsx), plus curl and git.
FROM node:22-slim AS skill-node

WORKDIR /app

RUN corepack enable pnpm

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY backend/skill-node/package.json ./backend/skill-node/package.json
COPY vendor/ ./vendor/
RUN pnpm install --frozen-lockfile --prod --filter @moldy/skill-node-runtime... --ignore-scripts

FROM node:22-slim AS node-runtime

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=skill-node /app/node_modules /app/node_modules
COPY --from=skill-node /app/backend/skill-node /app/backend/skill-node

ENV NODE_PATH=/app/backend/skill-node/node_modules
