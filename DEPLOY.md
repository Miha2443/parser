# Деплой на новом компьютере

## Что получишь после установки

- Сайт-дашборд на http://localhost:8501 со всеми данными
- Ежедневное автообновление в **06:00** через Task Scheduler
- Уведомления о результатах прогона в TDM
- Хранение архивов старых выгрузок в `data/raw/realty/_archive/`

## Требования

- **Windows 10/11**
- **Python 3.10+** (https://www.python.org/ — поставить галочку «Add to PATH»)
- **Google Chrome** (https://www.google.com/chrome/) — для Selenium-парсеров
- **Права администратора** (для регистрации Task Scheduler)
- **Интернет** + доступ к `api.tdm.mos.ru` (если из корпоративной сети — VPN)

## Установка (одной командой)

1. **Скачать репо**:
   ```cmd
   git clone <repo-url>
   cd parser
   ```

2. **ПКМ → Запуск от имени администратора** на `setup.bat`

Скрипт сделает 8 шагов:
| | Что | Время |
|---|---|---:|
| 1 | Проверка Python | 1с |
| 2 | Проверка Chrome | 1с |
| 3 | Создание `.venv` | 30с |
| 4 | `pip install -r requirements.txt` | 2-5 мин |
| 5 | Создание `.env` из шаблона | (открывает Блокнот) |
| 6 | Первичный сбор всех данных | 40-60 мин |
| 7 | Регистрация задачи в Task Scheduler | 5с |
| 8 | Запуск сайта | мгновенно |

На шаге 5 нужно вписать в `.env` 3 значения (см. ниже).

## Настройка TDM-бота (.env)

```ini
TDM_BOT_TOKEN=BOT-<токен_бота>
TDM_WORKSPACE_ID=<workspaceId>
TDM_GROUP_ID=<groupId>
```

### Как узнать workspaceId и groupId

1. Создай бота в TDM (через PrimeBot или админку), получи `BOT-<токен>`
2. Добавь бота в нужный чат
3. Запусти:
   ```cmd
   tdm_test.bat
   ```
   Он покажет все группы бота с их `groupId` и `workspaceId`.
4. Скопируй нужные значения в `.env`

## Ежедневный режим

После setup сайт работает сам:

- **06:00 каждый день** — Task Scheduler запускает `scripts\update_realty_scheduled.bat`:
  - Скачиваются свежие данные со всех источников
  - Только по **понедельникам** — долгий per-dev обход квартирографии (~90 мин)
  - В остальные дни — быстрый прогон (~20 мин)
  - Старые файлы переезжают в `_archive/<дата>/`
  - В TDM приходит сводка: что обновилось, что без изменений
- **Сайт** автоматически подхватывает свежие данные (TTL кеша 5 мин)

## Дополнительные команды

| Команда | Что делает |
|---|---|
| `start.bat` | Запустить сайт |
| `update.bat` | Прогон обновления вручную |
| `update.bat --skip-kvart-per-dev` | Прогон без долгого per-dev (~20 мин) |
| `tdm_test.bat` | Тест бота: показать группы + отправить тестовое |
| `scripts\register_scheduler.bat` | Зарегистрировать cron (от админа) |
| `scripts\register_scheduler.bat --unregister` | Удалить задачу |
| `schtasks /Run /TN "parser_etl_realty"` | Запустить задачу cron вручную |

## Логи

- **ETL логи**: `data\processed\etl_<YYYY-MM-DD>.log` (один файл на день)
- **Архив старых выгрузок**: `data\raw\realty\_archive\<дата>\<источник>\`
- **Streamlit**: вывод в окне `start.bat`

## Структура проекта

```
parser/
  setup.bat              ← одноразовый setup
  start.bat              ← запуск сайта
  update.bat             ← ручное обновление
  tdm_test.bat           ← тест бота
  DEPLOY.md              ← этот файл

  .env                   ← секреты (создаётся из .env.example)
  .env.example           ← шаблон

  app/                   ← Streamlit-приложение
    Home.py              ← главная
    pages/               ← 8 страниц
    data_access.py       ← загрузчики

  pipeline/
    tdm_notify.py        ← клиент TDM Bot API
    archive_old.py       ← архивация старых выгрузок
    selenium_utils.py    ← Chrome для парсеров

  scripts/
    update_realty.py     ← оркестратор всех парсеров
    update_realty_scheduled.bat       ← runner для cron
    register_scheduler.bat            ← регистрация Task Scheduler

  data/raw/realty/       ← свежие выгрузки
    nashdom/             ← monitoring 2.0, rasprodannost, kvartirografia
    erzrf/               ← top + cards
    escrow_manual/       ← ручная выгрузка ДОМ.РФ ЕИСЖС
    _archive/            ← старые версии (по датам)

  nashdom_checker.py     ← парсер наш.дом.рф
  erzrf_checker.py       ← парсер erzrf.ru
```

## Troubleshooting

**«Python не установлен»** — поставить https://www.python.org/ с галочкой «Add to PATH»

**«Chrome не найден»** — поставить https://www.google.com/chrome/

**TDM `getaddrinfo failed`** — нет доступа к `api.tdm.mos.ru`. Включить VPN мэрии или запускать с рабочего ПК

**Task Scheduler не регистрируется** — `setup.bat` нужно запустить ОТ ИМЕНИ АДМИНИСТРАТОРА

**ERZRF не качает** — нужна авторизация. См. `config/erzrf.example.json`, сохрани логин/пароль как `config/erzrf.json`

**Кеш Streamlit «застрял»** — в сайдбаре главной нажми «♻️ Перезагрузить кеш»

**Парсер падает** — посмотри `data\processed\etl_<YYYY-MM-DD>.log` или запусти `update.bat` вручную
