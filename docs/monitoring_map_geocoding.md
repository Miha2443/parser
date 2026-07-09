# Координаты для карты объектов monitoring 2.0

## Что уже делает проект

Карта берет объекты из `monitoring_2_0` и ищет координаты в таком порядке:

1. Использует готовую геометрию из листа ОКС, поле `Геометрия`.
2. Переносит геометрию ОКС на РВ по совпадению `Разрешение на строительство` ↔ `№РС`.
3. Переносит геометрию ОКС на РВ по точному совпадению адреса.
4. Берет координаты из кеша `data/derived/monitoring_geocodes.csv`.
5. Если у нескольких объектов одинаковый адрес, одна координата применяется ко всем этим объектам.

Это экономит запросы: геокодируется не каждый объект, а только уникальные адреса без координат.

## Почему нужен внешний геокодер

В листе РВ координат нет, там есть только адрес. Локальная сшивка с ОКС закрывает только часть РВ, поэтому для полного покрытия карты остаток нужно получать через геокодер. Для Москвы лучше использовать Яндекс Геокодер: он лучше понимает `вл.`, `з/у`, поселения Новой Москвы и строительные адреса, чем бесплатный Nominatim.

Важно: часть адресов слишком общая, например `г. Москва`, `поселение Десеновское`, `д. Столбово`. Геокодер для них вернет центр населенного пункта/территории, а не точку конкретного здания. Такие точки будут технически заполнены, но не будут точными до корпуса.

## 1. Получить ключ Яндекс Геокодера

1. Открой документацию Яндекса: https://yandex.com/maps-api/docs/geocoder-api/quickstart.html
2. Получи ключ для Geocoder API.
3. Дождись активации ключа. В документации Яндекса указано, что активация может занять до 15 минут.
4. Передай ключ скрипту одним из двух способов.

Вариант через переменную окружения:

```powershell
$env:YANDEX_GEOCODER_API_KEY="вставь_сюда_ключ"
```

Вариант через локальный файл:

```powershell
Copy-Item config\geocoder.example.json config\geocoder.local.json
notepad config\geocoder.local.json
```

В `config\geocoder.local.json` нужно заменить `paste_yandex_geocoder_key_here` на настоящий ключ. Этот файл добавлен в `.gitignore`, его не нужно коммитить.

## 2. Запустить полный прогон

В PowerShell из корня проекта:

```powershell
cd C:\Users\Mihail\Documents\парсер
.\scripts\run_monitoring_geocoding_yandex.ps1 -DryRun
```

Если `-DryRun` показал, что ключ найден, запускай полный прогон:

```powershell
.\scripts\run_monitoring_geocoding_yandex.ps1 -RestartServer
```

Эта команда:

1. Запустит геокодирование через Яндекс.
2. Будет сохранять кеш каждые 25 адресов.
3. Запустит проверку качества координат.
4. Перезапустит сайт на `8501`.

Если нужно вручную управлять параметрами:

```powershell
.\scripts\run_monitoring_geocoding_yandex.ps1 -Limit all -Sleep 0.05 -FlushEvery 25 -RestartServer
```

Если хочешь максимально защищенный режим, где результат сохраняется после каждого адреса:

```powershell
.\scripts\run_monitoring_geocoding_yandex.ps1 -FlushEvery 1 -RestartServer
```

Сейчас после фильтра ОКС с 2011 года, полного РВ и локальной сшивки покрытие примерно такое:

- всего объектов: `6 394`
- с координатами: `452`
- без координат: `5 942`
- уникальных адресов для внешнего геокодера: `3 957`

Точное число может измениться после обновления monitoring 2.0.

Если хочешь сначала проверить на маленькой пачке:

```powershell
python scripts\build_monitoring_geocodes.py --provider yandex --limit 50 --sleep 0.05
```

Скрипт можно прерывать и запускать снова. Уже найденные координаты остаются в `data/derived/monitoring_geocodes.csv`, повторно они не запрашиваются.

Бесплатный долгий вариант без Яндекса:

```powershell
python scripts\build_monitoring_geocodes.py --provider nominatim --limit all --sleep 1.2 --flush-every 10
```

## 2a. Вариант через внешний CSV

Если координаты получены не через встроенный Яндекс-запрос, а через другой инструмент, можно импортировать CSV.

Сначала выгрузи список уникальных адресов без координат:

```powershell
python scripts\build_monitoring_geocodes.py --provider none --missing-out data\derived\missing_geocode_addresses.csv
```

На выходе будет файл:

```text
data\derived\missing_geocode_addresses.csv
```

Минимально в CSV для обратного импорта должны быть колонки:

- `address` или `address_key`
- `lat`
- `lon`

Импорт:

```powershell
python scripts\build_monitoring_geocodes.py --provider none --import-csv data\derived\geocoded_addresses.csv
```

Скрипт сам размножит координаты на все объекты с тем же адресом и запишет их в `data/derived/monitoring_geocodes.csv`.

## 3. Проверить покрытие

```powershell
python -X utf8 -c "from app.realty_map import load_monitoring_map_objects; df=load_monitoring_map_objects(); print('objects', len(df)); print('with_coords', int(df['has_coords'].sum())); print('missing', int((~df['has_coords']).sum())); print(df[df['has_coords']]['coord_source'].value_counts(dropna=False).to_string())"
```

Ожидаемый результат после полного успешного геокодирования: `missing` должен стать близким к нулю. Если останутся пропуски, обычно это пустые или совсем нераспознаваемые адреса.

Для нормальной проверки качества лучше запустить отдельный валидатор:

```powershell
python scripts\check_monitoring_geocodes.py
```

Он создаёт два отчёта:

- `data\derived\monitoring_geocodes_missing.csv` — объекты без координат.
- `data\derived\monitoring_geocodes_suspicious.csv` — координаты вне допустимых границ Москвы/МО или низкая точность геокодера.

После полного прогона сначала смотри `missing`, потом `suspicious`. Если в `suspicious` попадут адреса с точностью `street`, `near`, `other` или `not_found`, их лучше проверить вручную.

## 4. Перезапустить сайт

```powershell
$conns = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
$procIds = $conns | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $procIds) { Stop-Process -Id $procId -Force }
Start-Sleep -Seconds 2
Start-Process -FilePath python -ArgumentList @('-m','streamlit','run','app/Home.py','--server.port','8501','--server.headless','true') -WorkingDirectory 'C:\Users\Mihail\Documents\парсер' -WindowStyle Hidden
```

Карта: http://localhost:8501/Карта_объектов

## Бесплатный режим

Можно использовать Nominatim:

```powershell
python scripts\build_monitoring_geocodes.py --provider nominatim --limit 100 --sleep 1.1
```

Но для полного покрытия это будет долго и менее точно. Nominatim хуже понимает московские строительные адреса и не подходит как основной способ для этой задачи.
