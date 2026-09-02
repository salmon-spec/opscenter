FROM node:22-alpine AS build

WORKDIR /src
RUN corepack enable && corepack prepare pnpm@10.15.1 --activate
COPY frontend-vite/package.json frontend-vite/pnpm-lock.yaml frontend-vite/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend-vite/index.html frontend-vite/vite.config.js ./
COPY frontend-vite/src ./src
RUN pnpm build

FROM caddy:2-alpine
COPY deploy/docker/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv/v3
