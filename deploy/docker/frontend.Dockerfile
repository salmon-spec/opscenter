# Frontend static bundle is built for the release and copied in directly.
#
# Why: on this target PVE host, Docker containers are confined by the default
# AppArmor profile which blocks Node.js child-process spawn channels
# (EACCES/ENOTCONN on the spawn socketpair). As a result pnpm/npm lifecycle
# scripts, the esbuild service and `vite build` cannot run inside any container
# (including during `docker build` RUN stages). The release archive therefore
# includes frontend-vite/dist and this image only packages it with Caddy.

FROM caddy:2-alpine
COPY deploy/docker/Caddyfile /etc/caddy/Caddyfile
COPY frontend-vite/dist /srv/v3
