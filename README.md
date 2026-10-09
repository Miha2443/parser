# Дашборд недвижимости — Debian 12

Рабочая версия состоит из статического сайта React/Vite (`frontend/`), API FastAPI
(`backend/`) и отдельного загрузчика всех автоматизированных источников
(`scripts/update_realty.py`). Загрузчик пишет в рабочие каталоги, а API читает
последний опубликованный набор через ссылку `live`. Сайт остаётся доступным во
время скачивания и сборки. После успешной проверки новый набор публикуется, и
перезапускается только API; открытой странице нужен `Ctrl+F5`, чтобы увидеть
новые данные. Браузер не должен оставаться открытым для ежедневного обновления.

Эта ветка предназначена для установки в `/opt/parser-dashboard` на Debian 12
x86_64. Linux-службы лежат в `deploy/linux/`.

В текущем состоянии ветки нет старых выгрузок из `downloads/`,
`data/raw/realty/nashdom/`, `data/raw/realty/erzrf/`, `nashdom/`, готовых
`data/processed/` и архива `_to_delete/`. При установке рабочие каталоги
создаёт `setup.sh`, а актуальные автоматические выгрузки получает полный
прогон. Эти пути добавлены в `.gitignore`: следующие `git pull` не подменяют
данные файлами из Git. В репозитории остаются
исторические статические таблицы в `data/raw/realty/vvod/`, ручная выгрузка
escrow, срез линейных объектов и справочники `data/derived/`: обычный прогон
не может восстановить их все из сети.

Обычный полный `git clone` скачивает и историю репозитория, в которой остались
старые выгрузки. Для новой установки ниже используются параметры `--depth 1`
и `--single-branch`: в рабочую папку и историю нового клона попадёт только
текущая версия ветки. На уже существующем сервере `git pull` уберёт старые
отслеживаемые файлы из рабочей папки, но не очистит историю `.git`.

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
sudo apt-get install -y git curl ca-certificates python3 python3-venv python3-pip chromium chromium-driver nginx rsync util-linux
python3 --version
chromium --version
chromedriver --version
```

Если Node.js 24 ещё не установлен, можно использовать репозиторий
[NodeSource для Debian](https://github.com/nodesource/distributions/blob/master/DEV_README.md):

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x -o /tmp/nodesource_setup_24.sh
sudo bash /tmp/nodesource_setup_24.sh
sudo apt-get install -y nodejs
sudo npm install -g pnpm@10
node --version
pnpm --version
```

Версии должны быть `v24.*` и `10.*`. Можно использовать доверенный репозиторий
организации с теми же версиями. Node и pnpm нужны только при сборке интерфейса.

## Установка из ветки

Команды ниже выполняются на Debian-сервере. Каталог и имя пользователя важны:
готовые unit-файлы ссылаются на `/opt/parser-dashboard` и `parserdash`.

```bash
id -u parserdash >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir /var/lib/parserdash --shell /bin/bash parserdash
sudo mkdir -p /opt/parser-dashboard
sudo chown parserdash:parserdash /opt/parser-dashboard
sudo -u parserdash -H git clone --depth 1 --single-branch --branch codex/linux-deployment https://github.com/Miha2443/parser.git /opt/parser-dashboard
sudo -u parserdash -H bash /opt/parser-dashboard/deploy/linux/setup.sh
```

Клонирование требует пустого `/opt/parser-dashboard`. `setup.sh` создаёт
`.venv`, устанавливает `requirements-linux.txt`, собирает `frontend/dist` в
режиме API и создаёт каталоги данных. Сначала добавьте личные настройки ниже,
затем установите службы: таймер может запустить обновление после включения.

Если проект уже есть в `/opt/parser-dashboard`, повторно клонировать поверх
него не нужно — смотрите «Обновление кода».

### Данные и настройки с действующей машины

Git **не переносит** автоматически загружаемые данные и секреты. Для чистой
установки старые `downloads/` и выгрузки ДОМ.РФ/ЕРЗ копировать не нужно:
запустите полный прогон после установки. Если нужен быстрый запуск сайта до
первого долгого скачивания или исторический ряд, перенесите **проверенные
актуальные** данные с действующей машины в одноимённые каталоги `downloads/`,
`data/raw/`, `data/processed/`, `data/marts/`, `data/derived/` и `state/`.
Сохраняйте даты файлов. Не переносите Windows `.venv`, `node_modules` и
временные `*.crdownload`. Переносите только доверенные `.pkl`: Python pickle
способен выполнить код при загрузке. После переноса назначьте владельца:

```bash
sudo chown -R parserdash:parserdash /opt/parser-dashboard/{downloads,data,state,logs}
```

Если отдельный источник недоступен, полный прогон сам не создаст его данные;
проверьте итоговый журнал и даты источников на сайте. Сборка интерфейса сама
по себе данные не обновляет.

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

После настройки выполните `install-services.sh`, как ниже. Не копируйте проект
поверх работающего экземпляра без резервной копии данных и конфигурации.

Необходимые личные настройки, если используются:

- `config/erzrf.json` — учётная запись для Excel-выгрузок ЕРЗ;
- `config/google-service-account.json` — если ваш маршрут мониторинга требует её;
- `config/geocoder.local.json` и `data/derived/monitoring_geocodes.csv` — координаты
  карты; геокодирование не входит в ежедневный прогон;
- `.env` — переменные TDM и загрузчиков. Шаблон `.env.example`; владельцем файла
  должен быть `parserdash`, режим доступа `600`.

Если `.env` нужен, создайте его до установки служб:

```bash
sudo -u parserdash -H cp /opt/parser-dashboard/.env.example /opt/parser-dashboard/.env
sudo chmod 600 /opt/parser-dashboard/.env
sudoedit /opt/parser-dashboard/.env
```

Пустые параметры TDM можно оставить незаполненными, если уведомления не нужны.
Секреты и личные файлы конфигурации не попадают в Git. Файлам с секретами
назначьте владельца `parserdash` и ограничьте права доступа.

Данные escrow в `data/raw/realty/escrow_manual/` **вводятся вручную**: у этого
источника нет автоматического загрузчика. Команда `all` обновляет все восемь
автоматизированных источников, но не создаёт ручную выгрузку escrow.

Теперь установите службы:

```bash
sudo bash /opt/parser-dashboard/deploy/linux/install-services.sh
sudo systemctl status parser-api.service nginx --no-pager
systemctl list-timers parser-update.timer
```

Установщик включает API, службу полного обновления, ежедневный таймер и
Nginx. До первой публикации `/health` отвечает, но страницы с данными могут
показывать `503`.

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

Первый и последующие **полные прогоны** запускайте через службу: она скачает
включённые источники, соберёт и проверит витрины, переключит `live` и
перезапустит API.

```bash
sudo systemctl start --no-block parser-update.service
sudo journalctl -fu parser-update.service
```

Он скачивает Мониторинг 2.0, квартирографию, текущее строительство и продажи,
топы/карточки ЕРЗ, Федстат и Росстат; затем строит `data/processed` и
`data/marts/realty`. Возможны долгие обходы — особенно квартирография и
распроданность. Упавшие источники повторяются по собственным правилам без
повторного запуска всей команды. Канонический журнал — `logs/update_*.log`,
статус — `data/processed/realty_update_status.json`. Код выхода `0` означает
успех; ненулевой требует изучить журнал. `Ctrl+C` закрывает только просмотр
журнала, служба продолжает работать. Проверка результата:

```bash
sudo systemctl status parser-update.service --no-pager
readlink -f /opt/parser-dashboard/live
sudo journalctl -u parser-update.service -n 100 --no-pager
```

Прямой запуск Python полезен для диагностики, но **сам по себе не публикует**
новые данные на сайте. Для диагностического запуска:

```bash
sudo -u parserdash -H bash -lc 'cd /opt/parser-dashboard && .venv/bin/python -u scripts/update_realty.py all'
```

Если запускали Python напрямую, после успеха выполните
`deploy/linux/publish-data.sh` от `parserdash` и перезапустите
`parser-api.service`. Параметр `--force` повторно запрашивает источники, но
**не расширяет** утверждённый набор показателей Федстата.

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
или проверка завершились ошибкой, `live` остаётся на прежнем выпуске. На чистой
установке до первого успешного прогона сайт будет без данных.

Чтобы пересобрать витрины из уже лежащих файлов **без скачивания** и сразу
опубликовать результат:

```bash
sudo -u parserdash -H bash -lc 'cd /opt/parser-dashboard && .venv/bin/python pipeline/orchestrator.py --skip-download && .venv/bin/python -m pipeline.build_realty_marts --strict && bash deploy/linux/publish-data.sh'
sudo systemctl restart parser-api.service
```

Если исходных файлов ещё нет, эта команда их не создаст: нужен полный прогон.

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

Перед первым переходом со старой Linux-версии сохраните рабочие данные и
секреты. При `git pull` Git уберёт из рабочей папки старые выгрузки, которые
раньше отслеживал; опубликованный `live` при этом остаётся прежним. Пример
резервной копии для уже установленного проекта:

```bash
backup_dir="/var/backups/parser-dashboard-$(date +%Y%m%d-%H%M%S)"
sudo mkdir -p "$backup_dir"
sudo rsync -a /opt/parser-dashboard/downloads /opt/parser-dashboard/data /opt/parser-dashboard/state /opt/parser-dashboard/config /opt/parser-dashboard/data-releases "$backup_dir/"
test ! -f /opt/parser-dashboard/.env || sudo cp -a /opt/parser-dashboard/.env "$backup_dir/"
sudo -u parserdash -H git -C /opt/parser-dashboard status --short
```

Если `git status` показывает изменённые **только автоматически созданные**
файлы в старых `downloads/`, `data/processed/`, `nashdom/`, `erzrf/` или
`_to_delete/`, их уже можно восстановить из резервной копии. В таком случае
перед `pull` верните только эти отслеживаемые пути к состоянию старого коммита:

```bash
sudo -u parserdash -H git -C /opt/parser-dashboard restore -- downloads data/processed data/raw/realty/nashdom data/raw/realty/erzrf nashdom _to_delete
```

Если изменены код, статические таблицы или настройки, разберите их отдельно:
эта команда их не трогает. **Не используйте `git reset --hard` для всего
проекта.** После резервного копирования обновите код:

```bash
sudo -u parserdash -H git -C /opt/parser-dashboard pull --ff-only origin codex/linux-deployment
sudo -u parserdash -H bash /opt/parser-dashboard/deploy/linux/setup.sh
sudo bash /opt/parser-dashboard/deploy/linux/install-services.sh
sudo systemctl start --no-block parser-update.service
```

`setup.sh` пересобирает интерфейс и проверяет зависимости;
`install-services.sh` перечитывает unit-файлы и Nginx-конфиг. Прогон через
службу заново получает актуальные автоматические источники и публикует их
после проверки. Следите за ним и проверьте результат командами из разделов
выше. Для следующих обновлений кода используйте те же команды; для обновления
**одних данных** `git pull` и пересборка React не нужны.

Старые Git-объекты в истории существующего клона остаются. Очистка рабочей
папки не переписывает историю репозитория.

Разработческая документация: [backend/README.md](backend/README.md),
[frontend/README.md](frontend/README.md), [состав показателей Федстата](docs/fedstat_dashboard_scope.md).
