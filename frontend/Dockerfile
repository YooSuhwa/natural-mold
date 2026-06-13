FROM node:22-alpine AS builder

WORKDIR /app

RUN corepack enable pnpm

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json ./frontend/package.json
COPY backend/skill-node/package.json ./backend/skill-node/package.json
RUN pnpm install --frozen-lockfile --filter frontend...

COPY frontend ./frontend
COPY backend/skill-node ./backend/skill-node

# NEXT_PUBLIC_* must be present at build time — Next inlines it into the client
# bundle, so a runtime env var alone won't reach the browser. Compose passes
# this via build args; the default suits local compose on localhost.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8001
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

WORKDIR /app/frontend
RUN pnpm build

FROM node:22-alpine AS runner

WORKDIR /app

RUN corepack enable pnpm

COPY --from=builder /app/frontend/.next/standalone ./
COPY --from=builder /app/frontend/.next/static ./.next/static
COPY --from=builder /app/frontend/public ./public

EXPOSE 3000

ENV NODE_ENV=production
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
