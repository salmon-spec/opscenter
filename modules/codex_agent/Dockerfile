FROM node:22-bookworm-slim AS codex
ARG CODEX_VERSION=0.166.0
RUN npm install -g --ignore-scripts @openai/codex@${CODEX_VERSION}
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git bash ca-certificates ripgrep && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home agent
COPY --from=codex /usr/local/lib/node_modules/@openai/codex /opt/codex
RUN ln -s /opt/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex /usr/local/bin/codex
ENV PYTHONPATH=/opt/module PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CODEX_HOME=/state/codex CODEX_WORKSPACE=/workspace
COPY modules/codex_agent/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY modules/runtime /opt/module/runtime
COPY modules/opencode_agent/vendor /opt/module/codex_agent/vendor
COPY modules/codex_agent /opt/module/codex_agent
WORKDIR /opt/module/codex_agent
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8080","--no-access-log"]
