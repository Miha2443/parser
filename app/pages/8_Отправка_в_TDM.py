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

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.components.design import apply_theme, page_header  # noqa: E402
from app.tdm_files import (  # noqa: E402
    list_tdm_datasets,
    list_tdm_realty_files,
    prepare_tdm_dataset,
)
from pipeline.tdm_notify import (  # noqa: E402
    notify, notify_file, get_all_groups,
    _get_token, _get_workspace_id, _get_group_id, _is_disabled,
)

st.set_page_config(page_title="Отправка в TDM — Аналитика Москвы", layout="wide")
apply_theme()
page_header("Отправка файлов в TDM")


@st.cache_data(show_spinner=False, ttl=60)
def _cached_tdm_realty_files(root: str):
    return list_tdm_realty_files(Path(root))


@st.cache_data(show_spinner=False, ttl=60)
def _cached_tdm_datasets(root: str):
    return list_tdm_datasets(Path(root))


# === Статус подключения ===
all_ok = bool(_get_token() and _get_workspace_id() and _get_group_id())
with st.expander("Статус подключения", expanded=not all_ok):
    cols = st.columns(4)
    token_ok = bool(_get_token())
    ws_ok = bool(_get_workspace_id())
    grp_ok = bool(_get_group_id())
    cols[0].metric("Токен бота", "готов" if token_ok else "нет",
                   help="TDM_BOT_TOKEN — authToken бота")
    cols[1].metric("Workspace ID", "готов" if ws_ok else "нет",
                   help="TDM_WORKSPACE_ID — ID пространства")
    cols[2].metric("Group ID", "готов" if grp_ok else "нет",
                   help="TDM_GROUP_ID — ID чата/группы")
    cols[3].metric("Состояние",
                   "disabled" if _is_disabled()
                   else "готов" if all_ok
                   else "настройка")

    if not all_ok:
        st.warning(
            "Нужны 3 env-переменные: **TDM_BOT_TOKEN**, "
            "**TDM_WORKSPACE_ID**, **TDM_GROUP_ID**.\n\n"
            "Создай `.env` (шаблон в `.env.example`) или установи переменные."
        )

    if token_ok and st.button("Получить список групп бота",
                              help="POST /botapi/v1/groups/getAllUserGroupStates"):
        with st.spinner("Запрашиваю список групп…"):
            try:
                groups = get_all_groups()
                if not groups:
                    st.info("Бот ещё не добавлен в группы. Добавь и повтори.")
                else:
                    st.success(f"Найдено {len(groups)} групп(ы):")
                    for g in groups:
                        gid = g.get("groupId")
                        group_obj = g.get("group") or {}
                        opp = g.get("opponent") or {}
                        ws = group_obj.get("workspaceId")
                        grp_type = group_obj.get("type", "—")
                        title = (group_obj.get("title")
                                 or (f"{opp.get('firstName', '')} "
                                     f"{opp.get('lastName', '')}").strip()
                                 or g.get("groupName") or "—")
                        st.code(
                            f"groupId={gid}\nworkspaceId={ws}\n"
                            f"тип: {grp_type}\nназв.: «{title}»",
                            language="text",
                        )
                    st.write(
                        "Скопируй нужный `groupId` и `workspaceId` в .env "
                        "(переменные `TDM_GROUP_ID` и `TDM_WORKSPACE_ID`)."
                    )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Ошибка API: {exc}")

st.markdown("---")

# === Выбор источника файла ===
st.markdown("### 1. Выбери данные")
source = st.radio(
    "Источник",
    ["Из раздела дашборда", "Выбрать исходный файл", "Загрузить с компьютера"],
    horizontal=True,
)

selected_path: Path | None = None
selected_dataset = None
selected_size: int | None = None
selected_name: str | None = None
upload_buffer = None

if source == "Из раздела дашборда":
    dataset_options = _cached_tdm_datasets(str(ROOT))
    if not dataset_options:
        st.info("Файлы, используемые дашбордом, пока не найдены")
    else:
        sections = list(dict.fromkeys(option.spec.section for option in dataset_options))
        section = st.selectbox("Раздел", sections)
        section_options = [option for option in dataset_options if option.spec.section == section]
        by_key = {option.spec.key: option for option in section_options}
        dataset_key = st.selectbox(
            "Данные / график",
            list(by_key),
            format_func=lambda key: by_key[key].spec.title,
        )
        if dataset_key:
            selected_dataset = by_key[dataset_key]
            selected_name = (
                selected_dataset.files[0].name if len(selected_dataset.files) == 1
                else f"{selected_dataset.spec.bundle_name}.zip"
            )
            selected_size = sum(path.stat().st_size for path in selected_dataset.files)
            st.success(f"Выбрано: **{selected_dataset.spec.title}**")
            with st.expander("Какие исходные файлы войдут", expanded=False):
                for path in selected_dataset.files:
                    st.code(str(path.relative_to(ROOT)), language="text")

elif source == "Выбрать исходный файл":
    realty_root = ROOT / "data" / "raw" / "realty"
    file_options = _cached_tdm_realty_files(str(realty_root))
    if not file_options:
        st.info("В data/raw/realty/ нет файлов")
    else:
        options_by_rel = {option.rel: option for option in file_options}
        choice = st.selectbox(
            "Файл",
            list(options_by_rel),
            format_func=lambda k: options_by_rel[k].label,
        )
        if choice:
            selected_path = options_by_rel[choice].path
            selected_name = selected_path.name
            selected_size = options_by_rel[choice].size_bytes

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

# === Override group_id (опционально) ===
with st.expander("⚙️ Дополнительно", expanded=False):
    override_group = st.text_input(
        "Отправить в другую группу (groupId)",
        placeholder=_get_group_id() or "по умолчанию из env",
        help="Можно отправить в группу отличную от TDM_GROUP_ID — "
             "узнать другие groupId через кнопку «Получить список групп» выше",
    )

# === Отправка ===
st.markdown("### 3. Отправить")

if selected_name and selected_size is not None:
    size_kb = selected_size / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
    st.info(f"Файл: **{selected_name}** ({size_str}) → "
            f"группа `{override_group or _get_group_id() or '—'}`")

ready = (
    (selected_dataset or selected_path or upload_buffer is not None)
    and _get_token()
    and (override_group or (_get_workspace_id() and _get_group_id()))
    and not _is_disabled()
)
btn = st.button(
    "Отправить в TDM",
    type="primary",
    disabled=not ready,
    use_container_width=True,
)

if btn:
    grp_to = (override_group.strip() or None) if override_group else None
    cap = caption.strip()
    with st.spinner("Отправляю в TDM…"):
        if selected_dataset:
            tmp_dir = ROOT / "data" / "_tmp_tdm"
            send_path, cleanup = prepare_tdm_dataset(selected_dataset, tmp_dir)
            try:
                ok = notify_file(send_path, caption=cap, group_id=grp_to)
            finally:
                if cleanup:
                    try:
                        send_path.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
        elif selected_path:
            ok = notify_file(selected_path, caption=cap, group_id=grp_to)
        else:
            tmp_dir = ROOT / "data" / "_tmp_tdm"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp = tmp_dir / upload_buffer.name
            tmp.write_bytes(upload_buffer.getvalue())
            try:
                ok = notify_file(tmp, caption=cap, group_id=grp_to)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
    if ok:
        st.success("Отправлено")
        st.balloons()
    else:
        st.error("Не удалось отправить. Проверь токен/workspace/group в .env "
                 "и логи в терминале где запущен Streamlit.")

# === Быстрая отправка текста ===
st.markdown("---")
st.markdown("### Или просто отправить текст")
text_only = st.text_area("Текст сообщения", key="text_only", height=80)
if st.button("Отправить текст",
             disabled=not (text_only and _get_token()
                           and _get_workspace_id() and _get_group_id())):
    with st.spinner("Отправляю…"):
        ok = notify(text_only,
                    group_id=(override_group or None) if override_group else None)
    if ok:
        st.success("Отправлено")
    else:
        st.error("Не удалось отправить")
