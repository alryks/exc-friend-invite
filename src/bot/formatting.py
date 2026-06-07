from __future__ import annotations

from datetime import datetime
from typing import Any


def format_job(job: dict[str, Any] | None) -> str:
    if not isinstance(job, dict):
        return "Не указана"
    facility = _first(job, "object", "facility", "объект", "facility_name", "name_object")
    position = _first(job, "position", "job", "должность", "profession", "name")
    if facility and position:
        return f"{facility} - {position}"
    return str(position or facility or job.get("id") or "Вакансия")


def format_status(app: dict[str, Any]) -> str:
    data = _data(app)
    status = app.get("status") or data.get("status")
    submitted = app.get("submitted") or data.get("submitted")
    if status == "accepted":
        return "Принята"
    if status == "declined":
        return "Отклонена"
    if status:
        return f"Статус: {status}"
    if submitted:
        return "Отправлена"
    return "Черновик"


def format_application_list_item(app: dict[str, Any]) -> str:
    data = _data(app)
    name = data.get("name") or app.get("name") or "Без имени"
    return f"{format_status(app)}: {name}\n{format_job(data.get('job') or app.get('job'))}"


def format_application_card(app: dict[str, Any], document_count: int | None = None) -> str:
    data = _data(app)
    doc_count = document_count if document_count is not None else _document_count(app)
    lines = [
        "Анкета кандидата",
        "",
        f"Должность: {format_job(data.get('job') or app.get('job'))}",
        f"ФИО рекомендателя: {data.get('referral') or 'Не указан'}",
        f"ФИО кандидата: {data.get('name') or 'Не указан'}",
        f"Пол: {data.get('gender') or 'Не указан'}",
        f"Телефон: {data.get('phone') or 'Не указан'}",
        f"Дата рождения: {date_api_to_human(data.get('age'))}",
        f"Дата прибытия: {date_api_to_human(data.get('date_on_object'))}",
        f"Гражданство: {data.get('residence') or 'Не указано'}",
        f"Документы: {doc_count} шт.",
        f"Комментарий: {data.get('comment') or 'Не указан'}",
    ]
    return "\n".join(lines)


def date_api_to_human(value: Any) -> str:
    if not value:
        return "Не указана"
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


def is_editable(app: dict[str, Any]) -> bool:
    data = _data(app)
    status = app.get("status") or data.get("status")
    submitted = bool(app.get("submitted") or data.get("submitted"))
    return not submitted or status == "declined"


def _data(app: dict[str, Any]) -> dict[str, Any]:
    data = app.get("data")
    return data if isinstance(data, dict) else app


def _document_count(app: dict[str, Any]) -> int:
    photo_ids = app.get("photo_ids") or _data(app).get("photo_ids")
    if isinstance(photo_ids, list):
        return len(photo_ids)
    if app.get("photo_pdf") or _data(app).get("photo_pdf"):
        return 1
    return 0


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None
