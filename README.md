# Parser Dashboard

React-дашборд с Python API и ETL-пайплайном для данных по Москве: строительство,
застройщики, распроданность, квартирография, ввод жилья, зарплата, ИПЦ,
ВРП/ВВП и служебная отправка файлов в TDM.

Проект рассчитан на Windows-машину, где по расписанию скачиваются Excel/JSON,
собираются быстрые витрины `data/marts/realty`, а сайт читает уже готовые
витрины и исходные файлы через общий слой доступа к данным. Прежний интерфейс
Streamlit сохранён отдельно для сравнения и отката.

## Что внутри

| Слой | Где | Назначение |
|---|---|---|
| Новый интерфейс | `frontend/` | React + TypeScript + Vite, графики ECharts, карта MapLibre |
| API | `backend/` | Python/FastAPI, данные страниц и полные выгрузки Excel |
| Прежний интерфейс | `app/` | Streamlit-сайт для сравнения и отката |
| Загрузчики | `*_checker.py`, `pipeline/downloaders/` | Скачивание raw-файлов из источников |
| Парсеры | `pipeline/parsers/` | Приведение Excel/JSON к единому DataFrame-формату |
| Оркестратор | `scripts/update_realty.py` | Запуск источников, retries, dedupe, архив, TDM-отчет |
| Витрины сайта | `pipeline/build_realty_marts.py` | Быстрые pickle-марты для realty-страниц |
| Проверки | `scripts/check_*.py`, `scripts/validate_realty_dashboard.bat` | Smoke/contract/self-check без ручного кликанья |

## Источники данных

| Источник | Что собираем |
|---|---|
| Мониторинг 2.0, ДОМ.РФ / Google Sheets | Реестр ОКС и РВ, объекты в строительстве и введенные объекты |
| Квартирография, наш.дом.рф | Комнатность по регионам, девелоперам и объектам |
| Распроданность, наш.дом.рф | Распроданность, стройготовность, прогнозы и сегменты |
| ERZRF top | Топы застройщиков по 5 сортировкам для РФ и Москвы |
| ERZRF cards | Карточки топ-100 застройщиков, переносы и ввод по годам |
| Эскроу, ДОМ.РФ ЕИСЖС | Ручная пообъектная выгрузка по Москве |
| Fedstat / Rosstat | Зарплата, ИПЦ, ВРП, ВВП и смежные статпоказатели |

## Запуск новой версии на Windows

Нужны Git, Python 3.12 с launcher `py` и Node.js 24 с npm. После установки
Python/Node.js заново откройте терминал. Команды ниже выполняются в PowerShell.

### Первый запуск на другом компьютере

```powershell
git clone --branch codex/data-marts-realty https://github.com/Miha2443/parser.git
cd parser
py --list
node --version
npm.cmd --version
Test-Path backend/requirements.txt
```

Последняя команда должна вернуть `True`. Если `False`, вы не в корне полного
проекта. Если `npm.cmd` не найден, установите Node.js и заново откройте терминал.
Если `py --list` не показывает Python 3.12, установите эту версию Python.

В первом терминале из корня проекта установите и запустите backend:

```powershell
py -3.12 -m venv .venv-dashboard
.\.venv-dashboard\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv-dashboard\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Во втором терминале из корня проекта установите и запустите frontend:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Откройте **http://localhost:5173/home**. API: **http://localhost:8000/docs**.
Если порт 5173 занят, используйте адрес, который Vite напечатал в терминале.
Оба терминала должны оставаться открытыми; остановка процесса: `Ctrl+C`.

На другом компьютере нужны также локальные данные, используемые страницами:
одного каталога `frontend` недостаточно. Локальные и игнорируемые Git файлы данных
не переносятся командой `git pull`. При отсутствии источника API показывает ошибку,
а не подставляет демонстрационные значения. Не используйте недоверенные pickle.

### Повторный запуск

Не создавайте среду и не устанавливайте зависимости заново при каждом запуске.
Первый терминал, из корня проекта:

```powershell
.\.venv-dashboard\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Второй терминал, из корня проекта:

```powershell
cd frontend
npm.cmd run dev
```

### Обновление до последней версии

1. Остановите backend и frontend через `Ctrl+C` в обоих терминалах.
2. Из корня проекта обновите нашу ветку:

```powershell
git switch codex/data-marts-realty
git pull --ff-only origin codex/data-marts-realty
```

3. Если изменились зависимости, повторите установку из `backend/requirements.txt`
   и `npm.cmd install` в `frontend`. Для пакета правок `dd332a4` это не требуется.
4. Выполните команды повторного запуска выше и нажмите `Ctrl+F5` в браузере.

Если Git сообщает о конфликте локальных изменений, не удаляйте их: сначала
разберите конфликт. В режиме разработки Vite обычно обновляет изменения
интерфейса автоматически, но после обновления обеих частей проекта надёжнее
перезапустить оба процесса. Для собранной версии требуется повторный
`npm.cmd run build` в `frontend`; перезагрузка браузера не пересобирает сайт.

API запускается только на локальном адресе и не предоставляет публичную
авторизацию. Не выставляйте API/TDM в интернет напрямую.

### Что изменилось в интерфейсе

- Главная: «Рынок недвижимости» и три группы страниц.
- Более заметные раскрывающиеся разделы боковой панели.
- Локальные фильтры у графиков; независимые месяцы ввода жилья и нежилья.
- Таблицы под графиками закрыты по умолчанию; выгрузки сохранены, дубли убраны.
- Подсказки по типам квартир, квартальная структура ввода и сравнение с АППГ.
- Исправлены пустые годы прогноза и масштаб осей статистических индексов.

Подробности: [frontend/README.md](frontend/README.md),
[backend/README.md](backend/README.md),
[дорожная карта](docs/dashboard-roadmap.md).

## Установка ETL и прежнего Streamlit-интерфейса

Этот раздел относится к загрузчикам данных и прежнему сайту на порту 8501.
`setup.bat` и `start.bat` не запускают новый React-интерфейс.

Требования:

- Windows 10/11
- Python 3.10+; рекомендуемый диапазон для установки зависимостей: 3.10-3.13
- Google Chrome для Selenium-источников
- Доступ к `api.tdm.mos.ru`, если нужны TDM-уведомления

Установка на новом компьютере:

```cmd
git clone <repo-url>
cd parser
setup.bat
```

`setup.bat` делает полный bootstrap:

| Шаг | Действие |
|---:|---|
| 1 | Проверяет Python |
| 2 | Проверяет Chrome |
| 3 | Создает `.venv` |
| 4 | Ставит зависимости из `requirements.txt` |
| 5 | Создает `.env` из `.env.example` |
| 6 | Скачивает данные или пересобирает их из локальных Excel |
| 7 | Регистрирует ежедневную задачу Windows Task Scheduler |
| 8 | Запускает сайт |

Полезные флаги:

```cmd
setup.bat --no-scrape       :: не скачивать данные, собрать из уже лежащих файлов
setup.bat --no-scheduler    :: не регистрировать расписание
setup.bat --no-start        :: не запускать сайт после установки
```

Если актуальные Excel/JSON уже лежат в `data/raw/realty`, можно запускать
`setup.bat --no-scrape`: он пересоберет processed-данные и realty-марты из
локальных файлов.

## Команды ETL и прежнего интерфейса

| Команда | Что делает |
|---|---|
| `start.bat` | Запускает прежний Streamlit-сайт на `http://localhost:8501` |
| `update.bat` | Полный ручной прогон источников, как daily-задача |
| `update.bat --plan` | Показывает план без скачивания |
| `update.bat nashdom` | Только источники наш.дом.рф |
| `update.bat erzrf` | Только ERZRF top/cards |
| `update.bat stats` | Только Fedstat/Rosstat |
| `update.bat --force` | Игнорирует state и пытается скачать заново |
| `update.bat --skip-kvart-per-dev` | Пропускает долгий per-dev обход квартирографии |
| `update.bat --full-rasprod-history` | Пересобирает всю историю распроданности |
| `scripts\build_realty_marts.bat` | Пересобирает быстрые realty-витрины |
| `scripts\build_realty_marts.bat --check` | Проверяет manifest и свежесть mart-файлов |
| `scripts\validate_realty_dashboard.bat` | Полная локальная проверка проекта |
| `tdm_test.bat` | Проверяет TDM-бота и выводит доступные группы |

Алиасы источников для точечных запусков:

```cmd
update.bat monitoring
update.bat rasprod
update.bat kvart
update.bat erz-top
update.bat erz-cards
update.bat fedstat
update.bat rosstat
```

Fedstat по умолчанию проверяет только 6 источников текущих графиков: зарплату,
ИПЦ и ввод жилья 34118. Остальные 24 сборщика сохранены и доступны через
`--only` / `FEDSTAT_ONLY_IDS`; `--force` не расширяет набор.
[Состав, отключённые показатели и включение позже](docs/fedstat_dashboard_scope.md).

[Возобновление Fedstat после 503 и watchdog](docs/fedstat_resilience.md).

[Навигация, темы и раскрываемые данные](docs/dashboard_navigation_20260930.md).


## Данные и витрины

Основные raw-файлы:

```text
data/raw/realty/
  nashdom/          monitoring_2_0_*.xlsx, kvartirografia_*.json, rasprodannost_*.xlsx
  erzrf/            top_*.xlsx, top_developers_*.json, cards/cards_*.xlsx
  escrow_manual/    ручная выгрузка ДОМ.РФ ЕИСЖС
  vvod/             статичные справочники ввода жилья
  _archive/         старые версии, перенесенные после успешного обновления
```

Быстрые витрины сайта:

```text
data/marts/realty/
  manifest.json
  monitoring_2_0.pkl
  kvartirografia.pkl
  rasprodannost.pkl
  erzrf_top.pkl
  erzrf_cards.pkl
  escrow_manual.pkl
  vvod_static.pkl
  emiss_34118.pkl
```

Сайт сначала пытается читать `data/marts/realty/*.pkl`. Если mart отсутствует
или помечен ошибкой, загрузчики могут откатиться к raw-файлам. Для строгой
проверки используйте:

```cmd
scripts\build_realty_marts.bat --check --strict
scripts\validate_realty_dashboard.bat
```

## Расписание

`setup.bat` регистрирует задачу `parser_etl_realty` на ежедневный запуск в
06:00 локального времени.

Что делает daily-прогон:

- запускает источники волнами, где это безопасно;
- повторяет упавшие источники;
- пишет machine-readable статус в `data/processed/realty_update_status.json`;
- удаляет дубликаты по контенту;
- архивирует старые raw-файлы в `_archive`;
- пересобирает только затронутые realty-марты;
- проверяет, что нет незавершенных `.tmp/.crdownload`;
- отправляет сводку в TDM, если настроен бот.

Per-dev обход квартирографии долгий, поэтому штатно включается по недельному
режиму через `scripts/update_realty_scheduled.bat`.

## Настройка секретов

### TDM

Скопируйте `.env.example` в `.env` и заполните:

```ini
TDM_BOT_TOKEN=
TDM_WORKSPACE_ID=
TDM_GROUP_ID=
```

Чтобы узнать `workspaceId` и `groupId`, добавьте бота в нужный чат и выполните:

```cmd
tdm_test.bat
```

### ERZRF

ERZRF требует авторизацию для Excel-выгрузок:

```cmd
copy config\erzrf.example.json config\erzrf.json
notepad config\erzrf.json
```

Формат:

```json
{
  "email": "you@example.com",
  "password": "password"
}
```

`config/erzrf.json` не коммитится.

## Проверки качества

Главная команда:

```cmd
scripts\validate_realty_dashboard.bat
```

Она проверяет:

- компиляцию Python-файлов;
- Windows wrapper-скрипты;
- атомарные записи JSON/XLSX/pickle/status;
- контракты downloader-оберток;
- корректность парсеров nashdom/erzrf;
- планирование `update_realty.py`;
- Streamlit runtime smoke для всех страниц;
- manifest и свежесть `data/marts/realty`;
- загрузку realty-данных из mart-файлов.

Быстрые точечные команды:

```cmd
python scripts\check_streamlit_pages_smoke.py
python scripts\check_realty_marts_smoke.py
python scripts\check_update_realty_plan.py
python scripts\check_nashdom_contract.py
python scripts\check_erzrf_atomic_outputs.py
```

## Структура проекта

```text
parser/
  app/
    Home.py
    data_access.py
    audit.py
    pages/
      1_Заработная_плата.py
      2_ИПЦ.py
      3_ВРП_и_ВВП.py
      4_Квартирография.py
      5_Квартирография_по_девелоперу.py
      6_Распроданность.py
      7_Профиль_застройщика.py
      8_Ввод_недвижимости.py
      8_Отправка_в_TDM.py
      99_Обновления.py

  pipeline/
    downloaders/
    parsers/
    build_realty_marts.py
    deduplicate.py
    selenium_utils.py
    tdm_notify.py

  scripts/
    update_realty.py
    validate_realty_dashboard.bat
    build_realty_marts.bat
    register_scheduler.bat
    register_scheduler_user.bat
    check_*.py

  data/
    raw/
    processed/
    marts/

  setup.bat
  start.bat
  update.bat
  tdm_test.bat
```

## Troubleshooting

| Симптом | Что сделать |
|---|---|
| Python не найден | Установить Python 3.10+ и включить `Add to PATH` |
| `pip install` упал с `IncompleteRead` | Это сетевой/PyPI-cache обрыв. Перезапустить `setup.bat`; setup делает 3 попытки и чистит pip cache |
| Setup выбрал Python 3.14+ | Установить Python 3.12 или 3.13 и перезапустить `setup.bat`; скрипт предпочитает стабильную 3.10-3.13 автоматически |
| Chrome не найден | Установить Google Chrome |
| ERZRF не скачивает Excel | Проверить `config\erzrf.json` |
| TDM не видит группы | Добавить бота в чат, написать сообщение, запустить `tdm_test.bat` |
| Сайт показывает старые данные | Нажать "Перезагрузить кеш" на главной или перезапустить `start.bat` |
| Mart stale/error | Запустить `scripts\build_realty_marts.bat --check`, затем обычный build |
| Остались `.crdownload` / `.tmp` | Дождаться завершения Chrome или удалить только явно незавершенную загрузку |
| Упал один источник | Запустить `update.bat <alias> --retries 3` |
| Нужно проверить все перед push | Запустить `scripts\validate_realty_dashboard.bat` |

Логи и статусы:

- `data/processed/etl_*.log`
- `data/processed/realty_update_status.json`
- `data/marts/realty/manifest.json`

## Разработка

Правило для изменений: сначала raw/contract/parser smoke, затем полный
`scripts\validate_realty_dashboard.bat`.

Перед изменениями загрузчиков особенно важно проверять:

- временные файлы скачивания не должны попадать в mart/build;
- битый новый файл не должен заменять валидный старый;
- выбор "последнего" файла должен быть привязан к дате в имени, если она есть;
- сайт должен открываться из mart-файлов без чтения тяжелых Excel на старте.
