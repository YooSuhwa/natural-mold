FROM node:22-slim AS skill-node

WORKDIR /app

RUN corepack enable pnpm

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY backend/skill-node/package.json ./backend/skill-node/package.json
RUN pnpm install --frozen-lockfile --prod --filter @moldy/skill-node-runtime... --ignore-scripts

FROM node:22-slim AS node-runtime

FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=skill-node /app/node_modules ./node_modules
COPY --from=skill-node /app/backend/skill-node ./backend/skill-node

ENV SKILL_NODE_BINARY=/usr/local/bin/node
ENV SKILL_NODE_MODULES_DIR=/app/backend/skill-node/node_modules

COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/README.md ./README.md
RUN uv sync --frozen --no-dev

COPY backend/app/ app/
COPY backend/alembic/ alembic/
COPY backend/alembic.ini .

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
