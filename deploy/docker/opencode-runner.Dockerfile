FROM node:22-bookworm-slim
ARG OPENCODE_VERSION=1.19.3
RUN apt-get update && apt-get install -y --no-install-recommends bash git python3 ca-certificates curl openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g --ignore-scripts opencode-ai@${OPENCODE_VERSION} \
    && ln -s /usr/local/lib/node_modules/opencode-ai/node_modules/opencode-linux-x64/bin/opencode /usr/local/bin/opencode-runner \
    && mkdir /workspace /runner-data && chown node:10001 /workspace /runner-data && chmod 2770 /workspace /runner-data
COPY modules/opencode_agent/opencode.json /etc/opencode/config.json
ENV OPENCODE_CONFIG=/etc/opencode/config.json OPENCODE_DISABLE_DEFAULT_PLUGINS=true \
    OPENCODE_DISABLE_EXTERNAL_SKILLS=true OPENCODE_DISABLE_PROJECT_CONFIG=false \
    GIT_CONFIG_GLOBAL=/runner-data/config/opscenter.gitconfig \
    XDG_DATA_HOME=/runner-data/data XDG_CONFIG_HOME=/runner-data/config \
    XDG_CACHE_HOME=/runner-data/cache XDG_STATE_HOME=/runner-data/state
USER node
WORKDIR /workspace
CMD ["sh", "-c", "set -eu; umask 0007; OPENCODE_SERVER_PASSWORD=$(cat /run/secrets/runner_password); test -n \"$OPENCODE_SERVER_PASSWORD\"; export OPENCODE_SERVER_PASSWORD; exec opencode-runner serve --hostname 0.0.0.0 --port 4096"]
