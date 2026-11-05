ARG BASE_IMAGE=opscenter/frontend:7a6dfdaf
FROM ${BASE_IMAGE}
COPY deploy/docker/Caddyfile /etc/caddy/Caddyfile
