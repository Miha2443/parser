# Parser Dashboard

Streamlit-дашборд и ETL-пайплайн для данных по Москве: строительство,
застройщики, распроданность, квартирография, ввод жилья, зарплата, ИПЦ,
ВРП/ВВП и служебная отправка файлов в TDM.

Проект рассчитан на Windows-машину, где по расписанию скачиваются Excel/JSON,
собираются быстрые витрины `data/marts/realty`, а сайт читает уже готовые
pickle-файлы вместо тяжелых исходников.

## Что внутри

| Слой | Где | Назначение |
|---|---|---|
| Дашборд | `app/` | Streamlit-сайт, страницы аналитики и статусы обновлений |
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

## Быстрый старт

Требования:

- Windows 10/11
- Python 3.10+
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

## Ежедневные команды

| Команда | Что делает |
|---|---|
| `start.bat` | Запускает сайт на `http://localhost:8501` |
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

