# ==============================================================================
# Frontend Dockerfile — Next.js App Router (Node 22 Alpine)
# Build context: ./frontend
#
# Supports both standard `next start` builds and `output: "standalone"`
# Next.js configurations automatically without requiring curl in runtime.
# ==============================================================================

FROM node:22-alpine AS deps

RUN apk add --no-cache libc6-compat
WORKDIR /app

COPY package*.json ./
RUN if [ -f package-lock.json ]; then \
        npm ci; \
    else \
        npm install; \
    fi


FROM node:22-alpine AS builder

WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL} \
    NEXT_TELEMETRY_DISABLED=1

RUN mkdir -p public /tmp/runtime-app && \
    npm run build && \
    if [ -f .next/standalone/server.js ]; then \
        cp -a .next/standalone/. /tmp/runtime-app/ && \
        mkdir -p /tmp/runtime-app/.next/static /tmp/runtime-app/public && \
        cp -a .next/static/. /tmp/runtime-app/.next/static/ && \
        cp -a public/. /tmp/runtime-app/public/; \
    else \
        if [ ! -f next.config.ts ]; then npm prune --omit=dev; fi && \
        cp -a package.json .next public node_modules /tmp/runtime-app/ && \
        cp next.config.* /tmp/runtime-app/ 2>/dev/null || true; \
    fi


FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 10001 nodejs && \
    adduser --system --uid 10001 --ingroup nodejs nextjs

COPY --from=builder --chown=nextjs:nodejs /tmp/runtime-app /app

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "const http = require('http'); const req = http.get('http://127.0.0.1:3000/', (res) => process.exit(res.statusCode >= 200 && res.statusCode < 400 ? 0 : 1)); req.on('error', () => process.exit(1)); req.setTimeout(3000, () => { req.destroy(); process.exit(1); });"]

CMD ["sh", "-c", "if [ -f server.js ]; then exec node server.js; else exec ./node_modules/.bin/next start -H 0.0.0.0 -p 3000; fi"]
