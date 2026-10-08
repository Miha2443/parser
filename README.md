# Дашборд недвижимости — Debian 12

Рабочая версия состоит из статического сайта React/Vite (`frontend/`), API FastAPI
(`backend/`) и отдельного загрузчика всех автоматизированных источников
(`scripts/update_realty.py`). Загрузчик пишет в рабочие каталоги, а API читает
последний опубликованный набор через ссылку `live`. Сайт остаётся доступным во
время скачивания и сборки. После успешной проверки новый набор публикуется, и
перезапускается только API; открытой странице нужен `Ctrl+F5`, чтобы увидеть
новые данные. Браузер не должен оставаться открытым для ежедневного обновления.

Эта ветка предназначена для установки в `/opt/parser-dashboard` на Debian 12
x86_64. Windows-команды и прежний Streamlit сохранены для действующей установки;
они описаны в [DEPLOY.md](DEPLOY.md). Linux-службы лежат в `deploy/linux/`.

## Что требуется

- Debian 12, пользователь с `sudo`, доступ к GitHub, npm/PyPI и сайтам источников.
- Python 3.11–3.13 (штатный Debian 12 — 3.11), Node.js **24** для сборки сайта,
  pnpm 10, Nginx, Chromium и соответствующий ChromeDriver.
- Достаточный ресурс для параллельных Chrome-процессов: начать с 4 vCPU, 16 ГБ RAM
  и 100 ГБ SSD, затем уточнить по реальному размеру `data/` и журналам прогонов.
- Доступ к ДОМ.РФ, ERZRF, Федстату и Росстату. Для TDM-уведомлений отдельно нужны
  реквизиты бота и доступ к `api.tdm.mos.ru`.

Пакеты Debian:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip chromium chromium-driver nginx rsync util-linux
```

Node.js 24 установите из доверенного для вашей организации репозитория или
официального дистрибутива Node.js. После проверки `node --version` установите
pnpm: `sudo npm install -g pnpm@10`. Дальше `node --version` должен показывать
`v24.*`, а `pnpm --version` — `10.*`. Node и pnpm нужны только при сборке нового
интерфейса, не как постоянно работающий сервер.

## Установка из ветки

Команды ниже выполняются на Debian-сервере. Каталог и имя пользователя важны:
готовые unit-файлы ссылаются на `/opt/parser-dashboard` и `parserdash`.

```bash
sudo useradd --system --create-home --home-dir /var/lib/parserdash --shell /bin/bash parserdash
sudo mkdir -p /opt/parser-dashboard
sudo chown parserdash:parserdash /opt/parser-dashboard
sudo -u parserdash -H git clone --branch codex/linux-deployment https://github.com/Miha2443/parser.git /opt/parser-dashboard
sudo -u parserdash -H bash /opt/parser-dashboard/deploy/linux/setup.sh
sudo bash /opt/parser-dashboard/deploy/linux/install-services.sh
```

`setup.sh` создаёт `.venv`, устанавливает `requirements-linux.txt`, собирает
`frontend/dist` в режиме API и создаёт каталоги данных. `install-services.sh`
ставит службу API, службу полного обновления, ежедневный таймер и Nginx-конфиг.
API до первой публикации отвечает на `/health`, но страницы с данными могут
показывать `503`. Если вы перенесли готовые данные, выполните публикацию ниже.
Первый запуск таймера не скачивает данные немедленно: полный прогон запускается
отдельной командой ниже. Если приложение уже есть в `/opt/parser-dashboard`,
сначала сохраните его данные и настройки и обновите код через Git; повторно
клонировать поверх существующего каталога не надо.

### Данные и настройки с действующей машины

Git **не переносит** игнорируемые файлы данных и секреты. Если нужен весь
исторический ряд сразу, до первой публикации перенесите в одноимённые каталоги
сервера `downloads/`, `data/raw/`, `data/processed/`, `data/marts/`, `data/derived/`
и `state/`. Сохраняйте дерево каталогов и даты файлов; не переносите Windows
`.venv`, `.venv-dashboard`, `node_modules` или временные `*.crdownload`. Переносите
только доверенные `.pkl`: Python pickle способен выполнить код при загрузке.
После переноса сделайте `sudo chown -R parserdash:parserdash /opt/parser-dashboard/{downloads,data,state,logs}`.

Если вы **уже скопировали файлы проекта** на сервер (например, ZIP распакован в
домашнем каталоге), вместо `git clone` перенесите содержимое в установленный
путь и проверьте структуру. Пример, где `/home/USER/parser/` — ваша папка:

```bash
sudo mkdir -p /opt/parser-dashboard
sudo rsync -a /home/USER/parser/ /opt/parser-dashboard/
sudo chown -R parserdash:parserdash /opt/parser-dashboard
test -f /opt/parser-dashboard/backend/requirements.txt
test -f /opt/parser-dashboard/frontend/package.json
sudo -u parserdash -H bash /opt/parser-dashboard/deploy/linux/setup.sh
```

Далее выполняйте `install-services.sh`, как выше. Не копируйте проект поверх
другого работающего экземпляра без резервной копии данных и конфигурации.

Необходимые личные настройки, если используются:

- `config/erzrf.json` — учётная запись для Excel-выгрузок ЕРЗ;
- `config/google-service-account.json` — если ваш маршрут мониторинга требует её;
- `config/geocoder.local.json` и `data/derived/monitoring_geocodes.csv` — координаты
  карты; геокодирование не входит в ежедневный прогон;
- `.env` — переменные TDM и загрузчиков. Шаблон `.env.example`; владельцем файла
  должен быть `parserdash`, режим доступа `600`.

Данные escrow в `data/raw/realty/escrow_manual/` **вводятся вручную**: у этого
источника нет автоматического загрузчика. Команда `all` обновляет все восемь
автоматизированных источников, но не создаёт ручную выгрузку escrow.

Если с действующей машины уже перенесены готовые `data/marts/realty` и
`data/processed`, сначала опубликуйте их, чтобы сайт заработал **до** первого
долгого скачивания:

```bash
sudo -u parserdash -H bash /opt/parser-dashboard/deploy/linux/publish-data.sh
sudo systemctl restart parser-api.service
```

Если перенесены только raw/Excel, сначала соберите витрины командой без скачивания
из раздела ниже, затем выполните `publish-data.sh`. Если данных ещё нет, запустите
полный прогон: он сам опубликует набор после успешной сборки.

## Полное скачивание и пересборка

Посмотреть план без обращения к источникам:

```bash
sudo -u parserdash -H bash -lc 'cd /opt/parser-dashboard && .venv/bin/python scripts/update_realty.py all --plan'
```

**Полный ручной прогон** всех автоматизированных источников и сборка витрин:

```bash
sudo -u parserdash -H bash -lc 'cd /opt/parser-dashboard && .venv/bin/python -u scripts/update_realty.py all'
```

Он скачивает Мониторинг 2.0, квартирографию, текущее строительство и продажи,
топы/карточки ЕРЗ, Федстат и Росстат; затем строит `data/processed` и
`data/marts/realty`. Возможны долгие обходы — особенно квартирография и
распроданность. Упавшие источники повторяются по собственным правилам без
повторного запуска всей команды. Канонический журнал — `logs/update_*.log`,
статус — `data/processed/realty_update_status.json`. Код выхода `0` означает
успех; ненулевой требует изучить журнал. `--force` повторно запрашивает данные,
но **не расширяет** утверждённый набор показателей Федстата.

Для обычной эксплуатации запускайте обновление **через службу** ниже: после
успеха она создаёт проверенный снимок в `data-releases/`, переключает `live` и
перезапускает API. Прямая команда Python выше полезна для диагностики, но после
неё публикацию нужно выполнить вручную командами из предыдущего блока.

Та же команда через установленную службу (она подхватит `.env`):

```bash
sudo systemctl start --no-block parser-update.service
sudo journalctl -fu parser-update.service
```

Повторный запуск, пока идёт прогон, не нужен: служба и сам оркестратор защищают
от наложения. Ежедневно в 06:00 **Europe/Moscow** команду запускает
`parser-update.timer`; пропущенный из-за выключенного сервера запуск будет
выполнен после старта. Проверка расписания:

```bash
systemctl list-timers parser-update.timer
systemctl status parser-update.service
```

Пока загрузчик работает, сайт продолжает читать прежний опубликованный каталог
`live`. Копирование в новый выпуск тоже происходит без остановки сайта; только
перезапуск API после переключения обычно занимает секунды. Старые выпуски
сохраняются в `data-releases/` для отката и занимают место на диске; одинаковые
файлы между соседними выпусками объединяются жёсткими ссылками. Если обновление
или проверка завершились ошибкой, `live` остаётся на прежнем выпуске.

Чтобы пересобрать витрины из уже лежащих файлов без скачивания:

```bash
sudo -u parserdash -H bash -lc 'cd /opt/parser-dashboard && .venv/bin/python pipeline/orchestrator.py --skip-download && .venv/bin/python -m pipeline.build_realty_marts --strict'
```

## Запуск и проверка сайта

API постоянно работает в `parser-api.service`; Nginx отдаёт собранный React и
проксирует `/api/` на `127.0.0.1:8000`. Состояние и логи:

```bash
sudo systemctl status parser-api.service nginx
sudo journalctl -fu parser-api.service
curl -f http://127.0.0.1:8000/api/v1/health
curl -f http://127.0.0.1:8000/api/v1/catalog
curl -f http://127.0.0.1:8080/home
```

`/health` проверяет только процесс. Успешный `/catalog` подтверждает, что API
видит опубликованные данные; дополнительно проверьте на сайте ввод, ИПЦ, зарплату, карту и
Excel-выгрузку. До первой публикации некоторые маршруты могут отвечать
`503`. После публикации уже открытую страницу нужно перезагрузить в браузере.

По умолчанию Nginx слушает **только localhost:8080**, чтобы не открыть API без
авторизации. Для просмотра со своего компьютера используйте SSH-туннель:

```bash
ssh -L 8080:127.0.0.1:8080 USER@SERVER
```

Затем откройте `http://127.0.0.1:8080/home`. Для доступа сотрудникам через сеть
администратору нужно настроить домен, TLS и корпоративную авторизацию/VPN,
после чего изменить `listen` в `deploy/linux/nginx-parser.conf` (или установленном
`/etc/nginx/sites-available/parser-dashboard.conf`) и проверить `nginx -t`.
Маршруты `/api/v1/tdm` в конфиге заблокированы: текущая защита TDM рассчитана
на локальный вызов и не годится за общим прокси. Уведомления **самого загрузчика**
через TDM при заданных реквизитах работают отдельно. Для удалённой отправки с
сайта понадобится авторизация пользователей в API.

Карта по умолчанию использует публичные тайлы OSM. Для постоянной эксплуатации
задайте одобренный сервис и атрибуцию через `VITE_MAP_TILE_URL` и
`VITE_MAP_ATTRIBUTION` **до** `pnpm build`; `VITE_*` встраиваются в браузерный код,
поэтому секреты туда помещать нельзя.

## Обновление кода

```bash
sudo -u parserdash -H bash -lc 'cd /opt/parser-dashboard && git pull --ff-only origin codex/linux-deployment && bash deploy/linux/setup.sh'
sudo systemctl restart parser-api.service
sudo nginx -t && sudo systemctl reload nginx
```

Если менялись unit-файлы или конфиг Nginx, повторите
`sudo bash /opt/parser-dashboard/deploy/linux/install-services.sh`. Изменение
данных не требует пересборки React; изменение React требует `pnpm build`.
Перед обновлением кода сохраняйте резервную копию `downloads/`, `data/`, `state/`
и личных конфигураций. Старые Linux-зависимые команды в `DEPLOY.md` относятся
к Windows-установке и не управляют этими службами.

Разработческая документация: [backend/README.md](backend/README.md),
[frontend/README.md](frontend/README.md), [состав показателей Федстата](docs/fedstat_dashboard_scope.md).
