FROM caddy:2-alpine
RUN setcap -r /usr/bin/caddy
COPY deploy/docker/portal-apps.Caddyfile /etc/caddy/Caddyfile
ENV XDG_CONFIG_HOME=/config XDG_DATA_HOME=/data
USER 10001:10001
EXPOSE 8081 8082 8083 8084 8085 8086
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
