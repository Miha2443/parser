#!/usr/bin/env bash
# This script destroys/recreates only its disposable CI project.
set -euo pipefail
[[ "${PARSER_COMPOSE_SMOKE:-}" == 1 && -f /.dockerenv ]] || exit 1
test "$(. /etc/os-release; echo "$VERSION_ID")" = 12
cd /opt/parser-dashboard
export COMPOSE_PROJECT_NAME=parser-compose-smoke
for script in docker/*.sh docker/tests/*.sh; do bash -n "$script"; done
printf 'OP_UID=1000\nOP_GID=1000\nTDM_DISABLED=1\n' > .env
bash docker/bootstrap.sh
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_docker_publication.py -v
docker compose run --rm --no-deps updater python -c "from pathlib import Path; assert Path('/app/data/derived/salary_2011_2012.csv').is_file(); assert Path('/app/data/raw/realty/linear_objects/linear_objects_2026-09-28.json').is_file()"
docker compose run --rm --no-deps updater python -c "from pathlib import Path; import fedstat_checker,rosstat_checker; fedstat_checker.save_state({'compose':True}); rosstat_checker.save_state({'compose':True}); assert Path('state/fedstat_state.json').is_file(); assert Path('state/rosstat_state.json').is_file()"
docker compose run --rm --no-deps updater python -c "import fedstat_checker,rosstat_checker; assert fedstat_checker.load_state()['compose']; assert rosstat_checker.load_state()['compose']"
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture seed
docker compose run --rm --no-deps updater python -m pipeline.build_realty_marts --strict
bash docker/update.sh --publish-existing
docker compose up -d
docker compose exec -T web nginx -t
sleep 3
docker compose exec -T backend python docker/readiness.py
backend_id="$(docker compose ps -q backend)"
[[ "$(docker inspect -f '{{len .Mounts}}' "$backend_id")" == 1 ]]
[[ "$(docker inspect -f '{{range .Mounts}}{{.Destination}}:{{.RW}}{{end}}' "$backend_id")" == '/runtime:false' ]]
[[ "$(docker compose ps --services --status running | sort | tr '\n' ' ')" == 'backend web ' ]]
test "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/api/v1/tdm/catalog)" = 403
test "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/api/unknown)" = 404
curl -fsS http://localhost:8080/api/v1/linear/catalog >/dev/null
previous="$(readlink runtime/live)"
export COMPOSE_FILE=docker-compose.yml:docker/tests/compose.fixture.yml
FIXTURE_FAIL=1 bash docker/update.sh && { echo 'Incomplete updater unexpectedly published'; exit 1; }
[[ "$(readlink runtime/live)" == "$previous" ]]
curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE OLD'
bash docker/update.sh > logs/compose-update.log 2>&1 &
update_pid=$!
sleep 2
curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE OLD'
wait "$update_pid"
[[ "$(readlink runtime/live)" != "$previous" ]]
curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE NEW'
# Force another IP by retaining the old IP on a disposable network peer.
backend_id="$(docker compose ps -q backend)"
network="${COMPOSE_PROJECT_NAME}_default"
old_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$backend_id")"
backend_image="$(docker inspect -f '{{.Config.Image}}' "$backend_id")"
docker compose rm -s -f backend
docker run -d --name dns-holder --network "$network" --ip "$old_ip" --entrypoint sleep "$backend_image" 120
docker compose up -d --no-deps backend
sleep 8
curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE NEW'
docker rm -f dns-holder
# Real publication succeeds, but a backend that refuses its new path must roll back.
cat > docker/tests/compose.rollback.yml <<'EOF'
services:
  backend:
    command: ["python", "-c", "import os,time; from pathlib import Path; from uvicorn import run; expected=os.environ['ROLLBACK_ALLOWED']; actual=str(Path('/runtime/live').resolve()); run('backend.app:app',host='0.0.0.0',port=8000) if actual==expected else time.sleep(300)"]
    environment:
      ROLLBACK_ALLOWED: ${ROLLBACK_ALLOWED}
EOF
export ROLLBACK_ALLOWED="/runtime/$(readlink runtime/live)"
previous="$(readlink runtime/live)"
export COMPOSE_FILE=docker-compose.yml:docker/tests/compose.fixture.yml:docker/tests/compose.rollback.yml
READINESS_TIMEOUT=25 bash docker/update.sh && { echo 'Unready backend unexpectedly accepted'; exit 1; }
[[ "$(readlink runtime/live)" == "$previous" && ! -f runtime/pending.json ]]
curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE NEW'
export COMPOSE_FILE=docker-compose.yml:docker/tests/compose.fixture.yml
docker compose up -d --no-deps backend
sleep 3
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture browser
docker compose down
echo 'Debian 12 Compose, browser, publication, readiness rollback and DNS checks passed.'
