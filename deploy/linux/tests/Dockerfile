FROM node:24-bookworm-slim AS node
FROM debian:12-slim
ENV container=docker
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd systemd-sysv dbus git curl ca-certificates \
    python3 python3-venv python3-pip chromium chromium-driver \
    nginx rsync util-linux tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/ /usr/local/
RUN npm install -g pnpm@10 \
    && useradd --system --create-home --home-dir /var/lib/parserdash --shell /bin/bash parserdash \
    && mkdir -p /opt/parser-dashboard \
    && chown parserdash:parserdash /opt/parser-dashboard \
    && systemctl set-default multi-user.target
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
