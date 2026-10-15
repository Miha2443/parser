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
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_optional_erz.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_docker_recovery.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_cron_install.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_source_availability.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_release_history.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_source_history.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_collector_browser_recovery.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_kvart_api.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_report_missing_values.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_commissioning_categories.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_monitoring_registry.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_erz_cards_coverage.py -v
docker compose run --rm --no-deps updater python -m unittest discover -s tests -p test_fedstat_export_recovery.py -v
for check in check_archive_old_collision.py check_deduplicate_cache.py check_erzrf_collector_contract.py check_fedstat_status.py check_fedstat_scope.py check_fedstat_transport.py check_kvart_resume.py check_nashdom_construction_operational.py check_nashdom_kvartirografia_parser.py check_nashdom_rasprod_api.py; do
  docker compose run --rm --no-deps updater python "scripts/$check"
done
docker compose run --rm --no-deps updater python -c "from pathlib import Path; assert Path('/app/data/derived/salary_2011_2012.csv').is_file(); assert Path('/app/data/raw/realty/linear_objects/linear_objects_2026-09-28.json').is_file()"
docker compose run --rm --no-deps updater python -c "from pathlib import Path; import fedstat_checker,rosstat_checker; fedstat_checker.save_state({'compose':True}); rosstat_checker.save_state({'compose':True}); assert Path('state/fedstat_state.json').is_file(); assert Path('state/rosstat_state.json').is_file()"
docker compose run --rm --no-deps updater python -c "import fedstat_checker,rosstat_checker; assert fedstat_checker.load_state()['compose']; assert rosstat_checker.load_state()['compose']"
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture seed-no-erz
bash docker/update.sh --publish-existing
! grep -q 'step=backend_restart' "$(ls -t logs/docker-update_*.log | head -n 1)"
docker compose up -d
docker compose exec -T web nginx -t
sleep 3
docker compose exec -T backend python docker/readiness.py
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture check-no-erz
# Restore the complete fixture for the existing failure/recovery lifecycle.
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture seed
bash docker/update.sh --publish-existing
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
before_identity="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")"
for failure in 1 missing-source empty-mart; do
  FIXTURE_FAIL="$failure" bash docker/update.sh && { echo "Incomplete updater unexpectedly published: $failure"; exit 1; }
  [[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")" == "$before_identity" ]]
  [[ "$(readlink runtime/live)" == "$previous" && ! -f runtime/pending.json ]]
  curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE OLD'
  docker compose exec -T backend python docker/readiness.py "/runtime/$previous"
done
bash docker/update.sh > logs/compose-update.log 2>&1 &
update_pid=$!
sleep 2
curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE OLD'
wait "$update_pid"
[[ "$(readlink runtime/live)" != "$previous" ]]
curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE NEW'
# Crash recovery must acknowledge an already healthy new backend without restart.
before_identity="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")"
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture recovery next
# An SSH paste can leave operator input attached; lifecycle clients must ignore it.
printf 'operator terminal input\n' | bash docker/update.sh --recover
[[ ! -f runtime/pending.json ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")" == "$before_identity" ]]
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture recovery clear
# A valid predecessor already served by the backend only needs its pointer restored.
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture recovery previous
bash docker/update.sh --recover
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")" == "$before_identity" ]]
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture recovery clear
# Empty previous is ambiguous with an existing legacy backend: never stop it.
# Legacy deployments read working marts; production publication now builds only
# its private candidate, so explicitly prepare the legacy fixture's data.
docker compose run --rm --no-deps updater python -m pipeline.build_realty_marts --strict
cat > docker/tests/compose.legacy.yml <<'EOF'
services:
  backend:
    environment:
      PARSER_ROOT: /app
      PARSER_DOWNLOADS: /app/downloads
    volumes:
      - ./data:/app/data:ro
      - ./downloads:/app/downloads:ro
EOF
export COMPOSE_FILE=docker-compose.yml:docker/tests/compose.fixture.yml:docker/tests/compose.legacy.yml
docker compose up -d --no-deps backend
for attempt in {1..20}; do
  docker compose exec -T backend python docker/readiness.py /app && break
  sleep 2
done
docker compose exec -T backend python docker/readiness.py /app
before_identity="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")"
bash docker/update.sh && { echo 'Legacy backend without journal unexpectedly allowed collection'; exit 1; }
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")" == "$before_identity" ]]
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture recovery empty
bash docker/update.sh && { echo 'Ambiguous bootstrap recovery unexpectedly continued'; exit 1; }
[[ -f runtime/pending.json ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")" == "$before_identity" ]]
curl -fsS http://localhost:8080/api/v1/catalog | grep -q 'COMPOSE NEW'
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture recovery clear
export COMPOSE_FILE=docker-compose.yml:docker/tests/compose.fixture.yml
docker compose up -d --no-deps backend
for attempt in {1..20}; do
  docker compose exec -T backend python docker/readiness.py "/runtime/$(readlink runtime/live)" && break
  sleep 2
done
docker compose exec -T backend python docker/readiness.py "/runtime/$(readlink runtime/live)"
before_identity="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")"
# A journal concerning another pair of releases cannot change the current pointer.
previous="$(readlink runtime/live)"
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture recovery unrelated
bash docker/update.sh --recover && { echo 'Unrelated recovery journal accepted'; exit 1; }
[[ "$(readlink runtime/live)" == "$previous" && -f runtime/pending.json ]]
[[ "$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "$(docker compose ps -q backend)")" == "$before_identity" ]]
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture recovery clear
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
# Exercise archiving through actual Compose DNS and the pinned backend health.
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture old-releases
docker compose run --rm --no-deps updater python -m docker.publication archive
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture check-archives
docker compose run --rm --no-deps -e PARSER_COMPOSE_SMOKE=1 updater python -m docker.tests.fixture browser
docker compose down
echo 'Debian 12 Compose, browser, publication, readiness rollback and DNS checks passed.'
