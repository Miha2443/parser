# Actual Debian 12 Docker Engine/Compose host, isolated inside the CI runner.
FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg util-linux coreutils \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable' > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    && groupadd -g 1000 op && useradd -m -u 1000 -g op -G docker op \
    && rm -rf /var/lib/apt/lists/*
CMD ["dockerd", "--storage-driver=overlay2"]
