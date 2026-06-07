from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


NAME_RE = re.compile(r"^[А-Яа-яЁё][А-Яа-яЁё \-]{1,148}[А-Яа-яЁё]$")
RESIDENCES = {"Россия", "Беларусь", "Киргизия", "Казахстан"}
GENDERS = {"Мужской", "Женский"}


def validate_cyrillic_name(value: str) -> bool:
    return bool(NAME_RE.fullmatch((value or "").strip()))


def normalize_ru_phone(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    return None


def parse_mm_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    return f"{parsed.isoformat()} 00:00:00"


def validate_application(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.get("job"):
        errors["job_id"] = "Выберите вакансию."
    if not validate_cyrillic_name(str(data.get("referral") or "")):
        errors["referral"] = "Введите ФИО кириллицей, можно использовать пробелы и дефисы."
    if not validate_cyrillic_name(str(data.get("name") or "")):
        errors["name"] = "Введите ФИО кириллицей, можно использовать пробелы и дефисы."
    if data.get("gender") not in GENDERS:
        errors["gender"] = "Выберите пол."
    if data.get("residence") not in RESIDENCES:
        errors["residence"] = "Выберите гражданство."

    phone = normalize_ru_phone(str(data.get("phone") or ""))
    if phone is None:
        errors["phone"] = (
            "Введите российский номер или оставьте поле пустым, "
            "а иностранный номер укажите в комментарии."
        )

    today = date.today()
    birth_date = _parse_api_date(data.get("age"))
    if not birth_date:
        errors["age"] = "Укажите дату рождения."
    elif birth_date > today:
        errors["age"] = "Дата рождения не может быть позже сегодняшней даты."

    arrival_date = _parse_api_date(data.get("date_on_object"))
    if not arrival_date:
        errors["date_on_object"] = "Укажите дату прибытия."
    elif arrival_date < today:
        errors["date_on_object"] = "Дата прибытия не может быть раньше сегодняшней даты."

    if len(str(data.get("comment") or "")) > 3000:
        errors["comment"] = "Комментарий не должен быть длиннее 3000 символов."
    return errors


def _parse_api_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
