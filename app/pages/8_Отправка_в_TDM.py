"""Отправка файлов в TDM-чат (TamTam-bot API).

Возможности:
  - Выбрать файл из data/ (свежие выгрузки)
  - Загрузить файл с компьютера
  - Добавить caption
  - Отправить в основной чат или указать другой chat_id
  - Превью статуса подключения (токен + chat_id)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.tdm_notify import (  # noqa: E402
    notify, notify_file, me, _get_token, _get_chat_id, _is_disabled,
)

st.set_page_config(page_title="Отправка в TDM — Аналитика Москвы", layout="wide")
st.title("📤 Отправка файлов в TDM")

# === Статус подключения ===
with st.expander("🔌 Статус подключения", expanded=not (_get_token() and _get_chat_id())):
    cols = st.columns(3)
    token_ok = bool(_get_token())
    chat_ok = bool(_get_chat_id())
    cols[0].metric("Токен бота", "✅ есть" if token_ok else "❌ нет",
                   help="TDM_BOT_TOKEN из env / .env")
    cols[1].metric("Chat ID", "✅ есть" if chat_ok else "❌ нет",
                   help="TDM_CHAT_ID из env / .env")
    cols[2].metric("Состояние",
                   "🚫 disabled" if _is_disabled()
                   else "✅ готов" if (token_ok and chat_ok)
                   else "⚠️ нужна настройка")

    if not token_ok or not chat_ok:
        st.warning(
            "Для работы нужны env-переменные **TDM_BOT_TOKEN** и **TDM_CHAT_ID**.\n\n"
            "Создай файл `.env` в корне проекта (есть шаблон `.env.example`) "
            "или установи переменные в окружении до запуска streamlit."
        )

    if token_ok and st.button("🧪 Проверить токен", help="Запрос /me к API"):
        with st.spinner("Запрашиваю /me..."):
            try:
                info = me()
                st.success(f"Бот: **{info.get('name', '?')}** "
                           f"(`@{info.get('username', '')}`)")
                with st.expander("Полный ответ API"):
                    st.json(info)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Ошибка API: {exc}")

st.markdown("---")

# === Выбор источника файла ===
st.markdown("### 1. Выбери файл")
source = st.radio(
    "Откуда взять файл",
    ["Из data/raw/realty/", "Загрузить с компьютера"],
    horizontal=True,
)

selected_path: Path | None = None
selected_size: int | None = None
selected_name: str | None = None
upload_buffer = None

if source == "Из data/raw/realty/":
    realty_root = ROOT / "data" / "raw" / "realty"
    files: list[Path] = []
    for ext in ("*.xlsx", "*.json", "*.csv", "*.pdf", "*.png"):
        files.extend(realty_root.rglob(ext))
    files = [f for f in files if "_archive" not in f.parts]
    if not files:
        st.info("В data/raw/realty/ нет файлов")
    else:
        # Сортируем по mtime (новые сверху)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        labels = {}
        for f in files:
            rel = f.relative_to(realty_root)
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            size_kb = f.stat().st_size / 1024
            size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            labels[str(rel)] = f"{rel}  ({mtime}, {size_str})"
        choice = st.selectbox(
            "Файл",
            list(labels.keys()),
            format_func=lambda k: labels[k],
        )
        if choice:
            selected_path = realty_root / choice
            selected_name = selected_path.name
            selected_size = selected_path.stat().st_size

else:  # Загрузить с компьютера
    upload_buffer = st.file_uploader(
        "Перетащи или выбери файл",
        type=None,  # любой
        accept_multiple_files=False,
    )
    if upload_buffer is not None:
        selected_name = upload_buffer.name
        selected_size = upload_buffer.size

# === Caption ===
st.markdown("### 2. Подпись к файлу (опционально)")
caption = st.text_area(
    "Текст сообщения",
    placeholder=f"Отчёт за {datetime.now().strftime('%B %Y')}…",
    height=100,
)

# === Override chat_id (опционально) ===
with st.expander("⚙️ Дополнительно", expanded=False):
    override_chat = st.text_input(
        "Отправить в другой чат (chat_id)",
        placeholder=_get_chat_id() or "по умолчанию из env",
        help="Можно отправить в чат отличный от TDM_CHAT_ID — "
             "узнать другой chat_id через `py -m pipeline.tdm_notify --list-chats`",
    )

# === Отправка ===
st.markdown("### 3. Отправить")

if selected_name and selected_size is not None:
    size_kb = selected_size / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
    st.info(f"📎 **{selected_name}** ({size_str}) → "
            f"chat `{override_chat or _get_chat_id() or '—'}`")

ready = (
    (selected_path or upload_buffer is not None)
    and _get_token()
    and (override_chat or _get_chat_id())
    and not _is_disabled()
)
btn = st.button(
    "🚀 Отправить в TDM",
    type="primary",
    disabled=not ready,
    use_container_width=True,
)

if btn:
    chat_to = (override_chat.strip() or None) if override_chat else None
    cap = caption.strip()
    with st.spinner("Отправляю в TDM…"):
        if selected_path:
            ok = notify_file(selected_path, caption=cap, chat_id=chat_to)
        else:
            # Загруженный файл — сохраняем во временный, шлём, удаляем
            tmp_dir = ROOT / "data" / "_tmp_tdm"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp = tmp_dir / upload_buffer.name
            tmp.write_bytes(upload_buffer.getvalue())
            try:
                ok = notify_file(tmp, caption=cap, chat_id=chat_to)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
    if ok:
        st.success("✅ Отправлено")
        st.balloons()
    else:
        st.error("❌ Не удалось отправить. Проверь токен/chat_id в .env "
                 "и логи в терминале где запущен Streamlit.")

# === Быстрая отправка текста ===
st.markdown("---")
st.markdown("### 💬 Или просто отправить текст")
text_only = st.text_area("Текст сообщения", key="text_only", height=80)
if st.button("Отправить текст", disabled=not (text_only and _get_token() and _get_chat_id())):
    with st.spinner("Отправляю…"):
        ok = notify(text_only, chat_id=(override_chat or None) if override_chat else None)
    if ok:
        st.success("✅ Отправлено")
    else:
        st.error("❌ Не удалось отправить")
