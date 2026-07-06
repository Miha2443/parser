"""
rosstat_checker.py
------------------
Скачивает xlsx-файлы со страниц Росстата (национальные счета) и Мосстата
(ВРП Москвы). На этих страницах нет fedstat-карточки с payload — просто
HTML-список ссылок с датой публикации рядом. Поэтому здесь без Selenium:
requests + BeautifulSoup.

Алгоритм один и тот же для каждой страницы:
1. GET, парсим HTML.
2. Находим все ссылки на .xlsx (и .xls на случай переключения формата).
3. Для каждой ссылки ищем рядом текст с датой формата DD.MM.YYYY.
4. Для каждого источника из PAGE_SOURCES ищем ссылку, у которой имя файла
   в URL (или любой вариант заголовка) содержит одну из подстрок match_any.
   Устойчиво к переименованиям вроде "(142)"/"(14)" в имени файла и к смене
   года в конце транслитного имени (VVP_god_s1995-2025 → ...-2026).
5. Сравниваем дату со state, при изменении — скачиваем.

State хранится в `state/rosstat_state.json` (отдельно от fedstat — у
источников разная природа).

Запуск:
    py rosstat_checker.py
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

from pipeline.file_utils import stream_response_atomic
from pipeline.state_utils import load_json_state, write_json_atomic

# Росстат использует сертификаты российского УЦ Минцифры, которых нет в
# стандартном trust store Python. Поскольку мы GET-им только публичные
# страницы и качаем xlsx (без передачи чувствительных данных), проверку
# отключаем — иначе на Windows скрипт падает с SSLError каждый раз.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VERIFY_SSL = False


PAGE_SOURCES: dict[str, list[dict]] = {
    "https://77.rosstat.gov.ru/folder/134924": [
        # На Мосстате имена файлов кириллицей: «ВРП с 1998 года(142).xlsx» и т.п.
        {"key": "vrp_msk",       "match_any": ["ВРП с 1998"]},
        {"key": "vds_msk_s2016", "match_any": ["ВДС годы ОКВЭД2 (с 2016"]},
    ],
    "https://www.rosstat.gov.ru/statistics/accounts": [
        # На Росстате актуальные файлы названы транслитом (VVP_god_s1995-2025.xlsx),
        # год в конце меняется — матчим по префиксу. Кириллический вариант оставлен
        # на случай, если когда-нибудь вернут читаемые имена.
        {"key": "vvp_god",       "match_any": ["VVP_god_s1995", "ВВП годы (с 1995"]},
        {"key": "vvp_na_dushu",  "match_any": ["VVP_na_dushu", "ВВП на душу населения"]},
        {"key": "vds_rf_s2011",  "match_any": ["VDS_god_OKVED2_s2011", "ВДС годы ОКВЭД2 (с 2011"]},
        # ВРП по регионам в старом ОКВЭД-2007 (2004-2016). Нужен для долей ВДС
        # Москвы за 2011-2015 (листы «2. 2011» … «2. 2015»). Парсер появится,
        # когда файл окажется в downloads/ — пока только скачивание.
        {"key": "vrp_okved2007", "match_any": ["VRP_OKVED2007", "VRP_god_OKVED2007", "ВРП ОКВЭД 2007"]},
    ],
}

DOWNLOAD_DIR = Path("downloads")
STATE_FILE = Path("rosstat_state.json")
REQUEST_TIMEOUT = 60
DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def load_state() -> dict:
    return load_json_state(STATE_FILE, label="rosstat")


def save_state(state: dict) -> None:
    write_json_atomic(STATE_FILE, state)


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })
    return s


_FILE_META_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:КБ|МБ|Кб|Мб|КИБ|байт|KB|MB|kb|mb|Kb|Mb)\b",
    flags=re.IGNORECASE,
)
_FILE_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
_FILE_EXT_RE = re.compile(r"\b(?:XLSX|XLS|PDF|DOCX|DOC|RAR|ZIP)\b", flags=re.IGNORECASE)
_XLSX_HREF_RE = re.compile(r"\.xlsx?(\?|$)", flags=re.IGNORECASE)
_PUA_RE = re.compile(r"[\ue000-\uf8ff]")  # иконочные шрифты Росстата


def _clean_label(raw: str) -> str:
    """Убирает из строки технические токены (размер файла, дата, расширение, иконки)."""
    s = _PUA_RE.sub(" ", raw)
    s = _FILE_META_RE.sub(" ", s)
    s = _FILE_DATE_RE.sub(" ", s)
    s = _FILE_EXT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" ,")


def _siblings_text(a) -> str:
    """Текст следующих siblings до следующего тега <a> (нужен когда родитель — общий контейнер списка)."""
    parts = []
    for sib in a.next_siblings:
        if getattr(sib, "name", None) == "a":
            break
        if hasattr(sib, "get_text"):
            parts.append(sib.get_text(" ", strip=True))
        else:
            parts.append(str(sib).strip())
    return " ".join(p for p in parts if p)


def _link_titles(a) -> list[str]:
    """Все возможные источники «названия» ссылки.

    На страницах Росстата визуальный заголовок («ВВП годы (с 1995 г.)») лежит
    не внутри тега <a>, а в соседнем span/div. Собираем варианты из разных
    мест и матчим по любому из них.
    """
    out: list[str] = []

    # Убираем символы приватной области Unicode (иконочные шрифты, напр. \ue2c0).
    def _strip_pua(s: str) -> str:
        return re.sub(r"[\ue000-\uf8ff]", "", s).strip()

    txt = _strip_pua(a.get_text() or "")
    if txt and not _FILE_EXT_RE.fullmatch(txt):
        out.append(txt)

    for attr in ("title", "download", "aria-label"):
        v = a.get(attr)
        if v and v.strip():
            out.append(v.strip())

    href = a.get("href") or ""
    base = href.rsplit("/", 1)[-1]
    base = unquote(base)
    if base:
        out.append(Path(base).stem)

    # Родитель — но только если он содержит не больше одной xlsx-ссылки
    # (иначе получим «мега-title» из всех соседних файлов).
    parent = a.parent
    if parent is not None:
        xlsx_in_parent = sum(
            1 for x in parent.find_all("a", href=True)
            if _XLSX_HREF_RE.search(x.get("href", ""))
        )
        if xlsx_in_parent <= 1:
            label = _clean_label(parent.get_text(" ", strip=True))
            if label:
                out.append(label)

    # Siblings до следующей xlsx-ссылки — для случая «иконки в ряд + общие тексты».
    sib_text = _clean_label(_siblings_text(a))
    if sib_text:
        out.append(sib_text)

    # Уникализируем, сохраняя порядок.
    seen: set[str] = set()
    unique: list[str] = []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _find_date_near(a) -> str:
    """Ищет дату DD.MM.YYYY рядом со ссылкой.

    Поднимается по родителям до 4 уровней и берёт первое попавшее совпадение
    в `get_text()` родительского узла. Если рядом несколько дат — берём
    последнюю (обычно «обновлено», а первая — «опубликовано»). Если ничего
    не нашли — возвращаем пустую строку.
    """
    node = a
    for _ in range(4):
        node = node.parent
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        matches = DATE_RE.findall(text)
        if matches:
            return matches[-1]
    return ""


def get_files_on_page(session: requests.Session, url: str) -> list[dict]:
    """Возвращает список {title, href, date} по всем xlsx/xls на странице."""
    print(f"  🌐 GET {url}")
    resp = session.get(url, timeout=REQUEST_TIMEOUT, headers={"Referer": url}, verify=VERIFY_SSL)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml" if _has_lxml() else "html.parser")

    items: list[dict] = []
    seen_hrefs: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not re.search(r"\.xlsx?(\?|$)", href, flags=re.IGNORECASE):
            continue
        absolute = urljoin(url, href)
        if absolute in seen_hrefs:
            continue
        seen_hrefs.add(absolute)
        titles = _link_titles(a)
        items.append({
            "title": titles[0] if titles else absolute,
            "titles": titles,
            "href": absolute,
            "date": _find_date_near(a),
        })
    print(f"     найдено xlsx-ссылок: {len(items)}")
    return items


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def _match_source(items: list[dict], needles) -> dict | None:
    """Первый item, у которого любой вариант названия (или URL) содержит любую из needles."""
    if isinstance(needles, str):
        needles = [needles]
    lowered = [n.lower() for n in needles]
    for it in items:
        haystack = [t.lower() for t in (it.get("titles") or [it.get("title", "")])]
        haystack.append(unquote(it.get("href", "")).lower())
        for n in lowered:
            if any(n in h for h in haystack):
                return it
    return None


def _debug_print_items(items: list[dict], limit: int = 8) -> None:
    """Печатает первые N найденных файлов с их вариантами названия и URL."""
    print(f"     ─── что нашлось (первые {min(limit, len(items))}): ───")
    for i, it in enumerate(items[:limit], 1):
        print(f"      {i}. titles={it.get('titles')}")
        print(f"         date={it.get('date')!r}  href={it.get('href')}")


def _parse_remote_date_to_yyyymmdd(s: str | None) -> str:
    """Конвертирует дату с сайта в YYYYMMDD. Fallback на сегодня."""
    if s:
        s = str(s).strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%Y %H:%M",
                    "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s[:len(fmt) + 6], fmt).strftime("%Y%m%d")
            except (ValueError, TypeError):
                continue
    return datetime.now().strftime("%Y%m%d")


def _filename_for(href: str, *, remote_date: str | None = None) -> str:
    """Имя сохраняемого файла: {YYYYMMDD}_{имя_из_url}.xlsx.

    YYYYMMDD = дата ОБНОВЛЕНИЯ ДАННЫХ с сайта Росстата (если есть),
    иначе — сегодняшняя дата.
    Имя берём из basename URL (а не текста <a>, который иконочный «XLSX»).
    """
    name = unquote(href.rsplit("/", 1)[-1])
    p = Path(name)
    stem = re.sub(r'[\\/*?:"<>|]', "", p.stem).strip()[:120]
    ext = p.suffix.lower() or ".xlsx"
    date_prefix = _parse_remote_date_to_yyyymmdd(remote_date)
    return f"{date_prefix}_{stem}{ext}"


def download_file(session: requests.Session, href: str, referer: str, save_path: Path) -> bool:
    """Скачивает файл в save_path. Возвращает True при успехе."""
    print(f"  ⬇️  {href}")
    try:
        resp = session.get(
            href,
            timeout=REQUEST_TIMEOUT,
            stream=True,
            headers={"Referer": referer},
            verify=VERIFY_SSL,
        )
        resp.raise_for_status()
        stream_response_atomic(resp, save_path)
        size_kb = save_path.stat().st_size // 1024
        print(f"  ✅ Сохранён: {save_path.name} ({size_kb} КБ)")
        return True
    except requests.RequestException as exc:
        print(f"  ❌ Ошибка скачивания: {exc}")
        return False


def run(force: bool = False) -> list[Path]:
    """force=True — игнорируем state, перекачиваем все источники."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state() if not force else {}
    session = _new_session()
    downloaded: list[Path] = []

    print(f"\n{'='*60}")
    print(f"rosstat_checker: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    for url, sources in PAGE_SOURCES.items():
        print(f"📄 Страница: {url}")
        try:
            items = get_files_on_page(session, url)
        except requests.RequestException as exc:
            print(f"  ❌ Не удалось загрузить страницу: {exc}\n")
            continue

        matched_any = False
        for src in sources:
            key = src["key"]
            needles = src["match_any"]
            print(f"  🔎 {key}: ищу {needles}")
            match = _match_source(items, needles)
            if not match:
                print(f"     ⚠️  не нашёл — пропускаю")
                continue
            matched_any = True

            remote_date = match["date"] or ""
            saved = state.get(key, {})
            saved_date = saved.get("date") if isinstance(saved, dict) else ""

            if remote_date and saved_date == remote_date:
                print(f"     ✔️  без изменений ({remote_date})")
                continue

            if not remote_date:
                # Дата не извлеклась — скачиваем безусловно при первом разе,
                # дальше будем сравнивать по hash/mtime если понадобится.
                print(f"     ℹ️  дата на странице не определена — скачиваю всё равно")
            elif not saved_date:
                print(f"     ℹ️  первая загрузка, дата {remote_date}")
            else:
                print(f"     🔄 обновился: {saved_date} → {remote_date}")

            filename = _filename_for(match["href"], remote_date=remote_date)
            save_path = DOWNLOAD_DIR / filename
            ok = download_file(session, match["href"], referer=url, save_path=save_path)
            if ok:
                downloaded.append(save_path)
                state[key] = {
                    "date": remote_date or saved_date or "",
                    "filename": filename,
                    "url": match["href"],
                }
            # Маленькая пауза, чтобы не долбить Росстат подряд.
            time.sleep(0.5)
        if not matched_any and items:
            _debug_print_items(items)
        print()

    save_state(state)

    print(f"{'='*60}")
    print(f"Итог: скачано — {len(downloaded)}")
    for p in downloaded:
        print(f"  • {p}")
    print(f"{'='*60}\n")
    return downloaded


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    run(force=force)
