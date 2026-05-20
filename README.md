# Аналитика Москвы — дашборд

Тестовый дашборд на Streamlit с двумя показателями:

- **Среднемесячная номинальная начисленная заработная плата** (fedstat 57824) — отрасли «Всего» и «Строительство», регионы РФ и Москва.
- **Индексы потребительских цен** (fedstat 31074, ч.1 + ч.2) — регионы РФ и Москва, типы индекса «к предыдущему месяцу» и «с начала года к АППГ».

Поддерживаются переключатели **Год / Квартал / Месяц**, переключатель **За период / С начала года** для ЗП, multi-select месяцев и кварталов, выбор отраслей и регионов, экспорт таблиц в Excel/CSV и графиков в PNG.

## Установка (Windows, Python 3.11–3.14)

В командной строке Windows используйте `py -m pip`, а не `pip` — последний часто не в PATH:

```cmd
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

Если на Python 3.14 не ставится `kaleido` — это не критично, просто пропустите его
(кнопка PNG-экспорта в дашборде не появится, всё остальное работает):

```cmd
py -m pip install pandas streamlit plotly openpyxl xlrd
```

## Использование

### Полный цикл (одной командой)

`py pipeline\orchestrator.py` делает всё:
1. Качает изменившиеся xls для каждого индикатора из `pipeline/registry.py`.
2. Парсит → перезаписывает витрину `data/processed/<id>.pkl`.
3. Пишет журнал в `data/processed/etl_audit.jsonl`.
4. (опционально) отправляет сводку в Telegram, если задан `config/telegram.json`.

Один упавший парсер не валит остальные — итог виден на странице «🔄 Журнал обновлений» дашборда.

### Без интернета — пересборка по уже скачанным xls

```cmd
py pipeline\orchestrator.py --skip-download
```

или (то же самое):

```cmd
py scripts\manual_ingest.py
```

### Запуск дашборда

```cmd
py -m streamlit run app\Home.py
```

Откройте http://localhost:8501.

### Ежедневное расписание (Windows Task Scheduler)

Из cmd от имени админа:

```cmd
schtasks /Create /SC DAILY /ST 06:00 /TN "parser_etl" /TR "C:\cloud\scripts\update_all.bat" /RL HIGHEST /F
```

`scripts\update_all.bat` — точка входа: пишет лог в `data\processed\etl.log` и аккуратно
обрабатывает зависшие запуски через lock-файл.

### Telegram-уведомления (опционально)

1. Создайте бота через `@BotFather`, узнайте `chat_id` (например, через `@userinfobot` для личных сообщений).
2. Скопируйте `config/telegram.example.json` в `config/telegram.json` и впишите `token` и `chat_id`.
3. Установите `requests`: `py -m pip install requests`.

Без файла модуль silent — оркестратор отрабатывает и без него.

## Структура

```
app/                    Streamlit-приложение
pipeline/               ETL: парсеры xls → Parquet
data/processed/         Витрина (Parquet, не коммитится)
downloads/              Сырые xls (вход)
fedstat_checker.py      Существующий парсер-загрузчик (Selenium + requests)
process_ipc.py          Старый DataLens-выход (оставлен на время миграции)
```

## Что дальше

После приёмки тестовой версии добавляем разделы из реестра показателей: ВРП, инвестиции, ввод жилья, домрф, ипотека и т.д. — без переписывания UI, через `pipeline/parsers/` и YAML-конфиг.
