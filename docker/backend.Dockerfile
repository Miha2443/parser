FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime

ARG UID=1000
ARG GID=1000
RUN groupadd -g "$GID" op && useradd -m -u "$UID" -g "$GID" op \
    && mkdir -p /app && chown op:op /app

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY --chown=op:op backend/ backend/
COPY --chown=op:op pipeline/ pipeline/
COPY --chown=op:op app/ app/
COPY --chown=op:op docker/ docker/

USER op
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PARSER_ROOT=/runtime/live \
    PARSER_DOWNLOADS=/runtime/live/downloads \
    PARSER_REQUIRE_REALTY_MARTS=1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
