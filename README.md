# Аналитика Москвы — дашборд

Streamlit-дашборд с данными о строительстве и социально-экономических
показателях Москвы. Восемь страниц с данными из 7 источников:

| Источник | Что |
|---|---|
| **Мониторинг 2.0** (Google Sheets, ДОМ.РФ) | реестр ОКС + РВ, объекты в строительстве и введённые |
| **Квартирография** (наш.дом.рф) | агрегаты по комнатности на застройщика и регион |
| **Распроданность** (наш.дом.рф) | распроданность / стройготовность / отношение Р/С |
| **ERZRF top** (erzrf.ru) | топы застройщиков по 5 сортировкам × 2 региона |
| **ERZRF cards** (erzrf.ru) | карточки топ-100 застройщиков, переносы по годам |
| **Эскроу** (ДОМ.РФ ЕИСЖС) | пообъектный реестр Москвы, кредитная нагрузка |
| **fedstat / rosstat** | ВРП, ВВП, ИПЦ, зарплата, население и пр. |

Уведомления о результатах прогонов парсеров — через TDM Bot API (мэрия Москвы).

---

## Быстрый старт на новом компе

### Требования

- Windows 10/11
- **Python 3.10+** — https://python.org (галочка «Add to PATH»)
- **Google Chrome** — https://google.com/chrome (для Selenium-парсеров)
- Доступ к `api.tdm.mos.ru` (для уведомлений, опционально)

### Установка одной командой

```cmd
git clone <repo-url>
cd parser
setup.bat
```

`setup.bat` пройдёт 8 шагов:

| | Что | Время |
|---|---|---:|
| 1 | Проверка Python ≥ 3.10 | 1с |
| 2 | Проверка Chrome | 1с |
| 3 | Создание `.venv` | 30с |
| 4 | `pip install -r requirements.txt` | 2-5 мин |
| 5 | `.env` из шаблона | ~1 мин (вписать токены) |
| 6 | Первичный сбор всех данных | **40-60 мин** |
| 7 | Регистрация задачи в Task Scheduler на 06:00 | 5с |
| 8 | Запуск сайта (`http://localhost:8501`) | мгновенно |

**От имени администратора** — Task Scheduler регистрируется как системный.
**Без прав админа** — Task Scheduler регистрируется как per-user
(сработает только когда юзер залогинен).

### Флаги setup

```cmd
setup.bat --no-scrape       :: не качать данные сразу (быстрый прогон)
setup.bat --no-scheduler    :: не регистрировать cron
setup.bat --no-start        :: не запускать сайт в конце
```

### Заполнение `.env` (TDM-бот)

После шага 5 в Блокноте откроется `.env`. Заполни **три переменные**:

```ini
TDM_BOT_TOKEN=BOT-<токен_бота>
TDM_WORKSPACE_ID=<workspaceId>
TDM_GROUP_ID=<groupId>
```

- **`TDM_BOT_TOKEN`** — токен бота. Выдаётся при создании бота в TDM.
  Имеет формат `BOT-<uuid>-<uuid>-<timestamp>-<uuid>`.

- **`TDM_WORKSPACE_ID` и `TDM_GROUP_ID`** — узнаются командой:
  ```cmd
  tdm_test.bat
  ```
  Перед запуском **добавь бота в нужный чат TDM** и напиши ему что-нибудь.
  В консоли увидишь:
  ```
  Найдено N групп(ы) бота:
    groupId=3220144879180380  workspaceId=-1  type=GROUP
      title: «Мой чат»  непрочитано: 1
  ```
  Скопируй `groupId` и `workspaceId` в `.env`.

### Заполнение `config\erzrf.json` (логин ERZRF)

Парсер `erzrf.ru` требует авторизации. Скопируй шаблон и впиши **свой логин/пароль**:

```cmd
copy config\erzrf.example.json config\erzrf.json
notepad config\erzrf.json
```

Внутри:
```json
{
  "email": "твой_email@example.com",
  "password": "твой_пароль_от_erzrf.ru"
}
```

Файл `config\erzrf.json` в `.gitignore` — секреты не уйдут в репо.

Если этот файл не заполнить, парсер ERZRF будет пропускаться (но
данные из других источников всё равно соберутся).

---

## Команды на каждый день

| Команда | Что делает |
|---|---|
| `start.bat` | Запустить сайт (http://localhost:8501) |
| `update.bat` | Прогон всех парсеров вручную (≈ daily cron в 06:00) |
| `update.bat fedstat` | Обновить только зарплату/ИПЦ |
| `update.bat --force` | Игнорировать state, пере-скачать всё |
| `update.bat --plan` | Показать источники, волны и затронутые realty-витрины без запуска |
| `update.bat --skip-kvart-per-dev` | Без долгого per-dev обхода (~20 мин) |
| `update.bat --full-rasprod-history` | Полный исторический пересбор распроданности вместо инкремента |
| `update.bat --retries 3` | Больше повторов для упавших источников |
| `scripts\build_realty_marts.bat` | Полностью пересобрать быстрые витрины сайта из `data\raw\realty` |
| `scripts\build_realty_marts.bat --check` | Проверить manifest/свежесть realty-витрин без пересборки |
| `python scripts\check_update_realty_plan.py` | Быстрый self-check планирования `update_realty.py` без сети |
| `python scripts\check_realty_marts_smoke.py` | Быстрый smoke-check загрузчиков realty-дашборда |
| `scripts\validate_realty_dashboard.bat` | Компиляция Python, plan-check, manifest-check и smoke-check realty-ветки одним запуском |
| `tdm_test.bat` | Проверить TDM-бота: список групп + тест |

### Группы источников для `update.bat`

```cmd
update.bat                          :: всё (7 источников)
update.bat nashdom                  :: monitoring + rasprod + kvart
update.bat erzrf                    :: erz-top + erz-cards
update.bat stats                    :: fedstat + rosstat
update.bat monitoring fedstat       :: точечно
```

---

## Ежедневный режим (Task Scheduler)

После `setup.bat` зарегистрирована задача `parser_etl_realty` на запуск
ежедневно в **06:00 локального времени**:

- Скачивает данные со всех источников
- Дедупликация: если сайт отдал тот же контент с новой датой — файл удаляется
- Старые версии переезжают в `data\raw\realty\_archive\<дата>\`
- После дедупликации пересобираются только витрины, чьи raw-файлы реально изменились
- Если уже есть `stale/error` mart, `update.bat` пересоберёт его даже без новых скачиваний
- Сборка витрин идёт в strict-режиме и не пройдёт при незавершённых `.tmp/.crdownload`
- Распроданность обновляется инкрементально: новые периоды + самый свежий месяц
- Selenium settle-паузы в `update.bat` масштабируются через `SELENIUM_SLEEP_SCALE` (по умолчанию `0.8`)
- **По понедельникам** — долгий per-dev обход квартирографии (~90 мин)
- В остальные дни — без него (~20 мин)
- Упавшие источники автоматически повторяются (до 2 раз с паузой 30/60 сек)
- В TDM приходит сводка с бизнес-темами: «Обновилось: ИПЦ, Квартирография, Мониторинг 2.0»

Сайт автоматически подхватывает свежие данные (кеш TTL 5 минут).
Кнопка «♻️ Перезагрузить кеш» в сайдбаре — для ручного сброса.

---

## Структура папок

```
parser/
  setup.bat          ← одноразовый setup
  start.bat          ← запустить сайт
  update.bat         ← ручное обновление
  tdm_test.bat       ← тест бота
  DEPLOY.md          ← подробная инструкция

  .env               ← секреты (не коммитится)
  .env.example       ← шаблон

  app/               ← Streamlit
    Home.py          ← главная (со свежестью данных в сайдбаре)
    data_access.py   ← загрузчики
    pages/
      1_Заработная_плата.py
      2_ИПЦ.py
      3_ВРП_и_ВВП.py
      4_Квартирография.py
      5_Квартирография_по_девелоперу.py
      6_Распроданность.py
      7_Профиль_застройщика.py    ← главная страница профиля
      8_Отправка_в_TDM.py          ← отправка файлов в TDM
      99_Обновления.py

  pipeline/
    tdm_notify.py    ← клиент TDM Bot API
    archive_old.py   ← архивация старых выгрузок
    build_realty_marts.py ← быстрые витрины realty для Streamlit
    deduplicate.py   ← дедупликация по контенту
    selenium_utils.py ← create_chrome + retry_with_refresh
    parsers/         ← парсеры xls/json в DataFrame

  scripts/
    update_realty.py              ← оркестратор всех парсеров
    build_realty_marts.bat        ← ручная пересборка data\marts\realty
    update_realty_scheduled.bat   ← runner для cron (грузит .env, venv)
    register_scheduler.bat        ← регистрация Task Scheduler (admin)
    register_scheduler_user.bat   ← per-user задача (без admin)

  data/raw/realty/
    nashdom/         ← monitoring_2_0, rasprodannost, kvartirografia
    erzrf/           ← top_* + cards/
    escrow_manual/   ← ручная выгрузка ДОМ.РФ ЕИСЖС
    _archive/        ← старые версии по датам

  downloads/         ← fedstat + rosstat xls

  nashdom_checker.py ← парсер наш.дом.рф (selenium)
  erzrf_checker.py   ← парсер erzrf.ru (selenium)
  fedstat_checker.py ← парсер fedstat.ru (selenium)
  rosstat_checker.py ← парсер rosstat.gov.ru (requests)
```

---

## Troubleshooting

**Python не установлен** — поставить https://python.org с галочкой «Add to PATH»

**Chrome не найден** — поставить https://google.com/chrome

**TDM `getaddrinfo failed`** — нет доступа к `api.tdm.mos.ru`. Включи
VPN мэрии или запусти с рабочего ПК

**Task Scheduler не регистрируется** — `setup.bat` нужно запустить
от админа, или используется per-user fallback

**Зарплата/ИПЦ не обновляются** — `update.bat fedstat --force`
игнорирует state и качает заново

**Кеш Streamlit «застрял»** — в сайдбаре главной нажми «♻️ Перезагрузить кеш»

**Парсер падает на одном источнике** — `--retries 3` или запустить только
его: `update.bat <alias>` (alias: monitoring, rasprod, kvart, erz-top,
erz-cards, fedstat, rosstat)

**ERZRF не качает** — нет `config\erzrf.json`. Скопируй
`config\erzrf.example.json` → `config\erzrf.json`, впиши свой логин/пароль
от erzrf.ru

**Не вижу TDM groupId** — добавь бота в чат TDM и напиши ему сообщение,
потом `tdm_test.bat`. Если бот ещё ни в одних чатах, команда выведет
«пусто»

**Логи прогонов** — `data\processed\etl_<YYYY-MM-DD>.log`

---

## Старая инструкция / разработка

Подробности про парсеры, переменные окружения и архитектуру —
в `DEPLOY.md` и докстрингах модулей.
