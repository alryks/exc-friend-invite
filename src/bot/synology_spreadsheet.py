from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SynologySpreadsheetExporter:
    def __init__(
        self,
        *,
        api_host: str,
        username: str,
        password: str,
        host: str,
        protocol: str,
        spreadsheet_id: str,
        sheet_name: str,
        timeout_seconds: float = 10,
    ) -> None:
        self._api_host = api_host.rstrip("/")
        self._credentials = {
            "username": username,
            "password": password,
            "host": host,
            "protocol": protocol,
        }
        self._spreadsheet_id = spreadsheet_id
        self._sheet_name = sheet_name
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_seconds))
        self._token: str | None = None
        self._queue: queue.Queue[list[Any]] = queue.Queue(maxsize=1000)
        self._worker = threading.Thread(
            target=self._work,
            name="synology-spreadsheet-worker",
            daemon=True,
        )
        self._worker.start()

    def enqueue(self, row: list[Any]) -> None:
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            logger.error("Synology spreadsheet queue is full; row was dropped")

    def _work(self) -> None:
        while True:
            row = self._queue.get()
            try:
                self._append_with_retry(row)
            except Exception:
                logger.exception("Failed to append candidate snapshot to Synology spreadsheet")
            finally:
                self._queue.task_done()

    def _append_with_retry(self, row: list[Any]) -> None:
        for attempt in range(2):
            try:
                if self._token is None:
                    self._token = self._authorize()
                response = self._client.put(
                    f"{self._api_host}/spreadsheets/{self._spreadsheet_id}/values/"
                    f"{self._sheet_name}!A:M/append",
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"values": [row]},
                )
                response.raise_for_status()
                return
            except (httpx.HTTPError, KeyError, ValueError):
                self._token = None
                if attempt == 1:
                    raise

    def _authorize(self) -> str:
        response = self._client.post(
            f"{self._api_host}/spreadsheets/authorize",
            json=self._credentials,
        )
        response.raise_for_status()
        token = response.json().get("token")
        if not token:
            raise ValueError("Synology authorization response has no token")
        return str(token)


def candidate_snapshot_row(
    employee_name: str,
    data: dict[str, Any],
    pdf_url: str = "",
    *,
    now: datetime | None = None,
) -> list[Any]:
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return [
        timestamp,
        employee_name,
        _format_job(data.get("job")),
        data.get("referral", ""),
        data.get("name", ""),
        data.get("gender", ""),
        data.get("phone", ""),
        _format_date(data.get("age")),
        _format_date(data.get("date_on_object")),
        data.get("residence", ""),
        data.get("comment", ""),
        pdf_url,
        "Да" if data.get("submitted", False) else "Нет",
    ]


def _format_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


def _format_job(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    facility = _first(value, "объект", "object", "facility", "facility_name", "name_object")
    position = _first(value, "должность", "position", "job", "profession", "name")
    gender = _first(value, "пол", "gender")
    return "|".join(str(part) for part in (facility, position, gender) if part not in (None, ""))


def _first(data: dict[str, Any], *keys: str) -> Any:
    return next((data[key] for key in keys if data.get(key) not in (None, "")), None)
