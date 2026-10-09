FROM python:3.12-slim-bookworm

ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime

ARG UID=1000
ARG GID=1000

# Тот же набор, что deploy/linux/setup.sh требует на хосте: Chromium +
# chromedriver из apt (версии совместимы друг с другом по умолчанию в
# рамках одного Debian-релиза) плюс библиотеки для headless-рендера.
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium chromium-driver tzdata \
      fonts-liberation libnss3 libatk-bridge2.0-0 libgtk-3-0 \
      libasound2 libxss1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g "$GID" op && useradd -m -u "$UID" -g "$GID" op \
    && mkdir -p /app && chown op:op /app

WORKDIR /app

COPY requirements-linux.txt .
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r requirements-linux.txt

COPY --chown=op:op . .
# Seeds live outside /app/data, which the mutable host directory hides.
RUN mkdir -p /opt/parser-seed && cp -a data /opt/parser-seed/ \
    && chown -R op:op /opt/parser-seed \
    && rm -f fedstat_state.json rosstat_state.json

USER op
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PARSER_ROOT=/app \
    HEADLESS_MODE=new

CMD ["python", "-u", "-m", "docker.publication", "update"]
