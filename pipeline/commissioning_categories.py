"""Source taxonomy for housing commissioning; totals are never a MKD proxy."""
from __future__ import annotations

import re
import unicodedata

EMISS_CATEGORIES = ("TOTAL", "ИЖС", "ИЖС_сельское", "МКД", "Жилые_здания")


def category_label(value):
    text = unicodedata.normalize("NFKC", str(value)).casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", text.replace(",", " ")).strip()


def emiss_category(value):
    label = category_label(value)
    if label == "жилые здания жилые помещения в нежилых зданиях и жилые дома построенные населением":
        return "TOTAL"
    if "сельской местности" in label and "построенн" in label and "населени" in label:
        return "ИЖС_сельское"
    if label in {"жилые дома построенные населением", "жилые дома построенных населением", "построенные населением"}:
        return "ИЖС"
    if label == "мкд" or "многоквартирн" in label:
        return "МКД"
    if label == "жилые здания":
        return "Жилые_здания"
    return None
