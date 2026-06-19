from __future__ import annotations

import base64
import logging
from typing import Any
from uuid import uuid4

import httpx


logger = logging.getLogger(__name__)


class FriendApiError(Exception):
    pass


class FriendApiUnavailable(FriendApiError):
    pass


class FriendApiValidationError(FriendApiError):
    pass


class FriendApiClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float = 10,
        mock_mode: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._mock_mode = mock_mode
        self._mock_apps: dict[str, dict[str, Any]] = {}
        headers = {"X-API-KEY": api_key} if api_key else {}
        self._client = httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
        )

    def create_app(self) -> str:
        payload = self._post("/create_app", {})
        application_id = payload.get("application_id") or payload.get("id")
        if not application_id:
            raise FriendApiValidationError("Friend API did not return application_id")
        return str(application_id)

    def get_app(self, application_id: str) -> dict[str, Any]:
        return self._post("/get_app", {"application_id": application_id})

    def set_app(self, application_id: str, data: dict[str, Any]) -> None:
        self._post("/set_app", {"application_id": application_id, "data": data})

    def delete_app(self, application_id: str) -> None:
        self._post("/delete_app", {"application_id": application_id})

    def add_app_photo(self, application_id: str, content: bytes) -> None:
        photo = base64.b64encode(content).decode("ascii")
        self._post("/add_app_photo", {"application_id": application_id, "photo": photo})

    def clear_app_photo(self, application_id: str) -> None:
        self._post("/clear_app_photo", {"application_id": application_id})

    def get_app_photo(self, application_id: str) -> dict[str, Any]:
        return self._post("/get_app_photo", {"application_id": application_id})

    def get_user_apps(self, user_id: int) -> list[dict[str, Any]]:
        payload = self._post("/get_user_apps", {"tg_id": user_id})
        apps = payload.get("applications", payload.get("apps", payload))
        return apps if isinstance(apps, list) else []

    def get_jobs(self) -> list[dict[str, Any]]:
        payload = self._post("/get_jobs", {})
        jobs = payload.get("jobs", payload.get("data", payload))
        return jobs if isinstance(jobs, list) else []

    def get_facility_binds(self) -> list[dict[str, Any]]:
        payload = self._post("/get_facility_binds", {})
        binds = payload.get("binds", payload.get("data", payload))
        return binds if isinstance(binds, list) else []

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._mock_mode:
            logger.debug("Friend API mock mode is enabled")
            return self._mock_response(endpoint, payload)

        for attempt in range(3):
            try:
                response = self._client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                return self._validate_response(data)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                if attempt == 2:
                    raise FriendApiUnavailable(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise FriendApiUnavailable(str(exc)) from exc
            except ValueError as exc:
                raise FriendApiValidationError("Friend API returned invalid JSON") from exc
        raise FriendApiUnavailable("Friend API unavailable")

    def _validate_response(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {"data": data}
        status = data.get("status")
        if status and status not in ("ok", "success"):
            raise FriendApiValidationError(str(data.get("error") or data))
        if data.get("error"):
            raise FriendApiValidationError(str(data["error"]))
        return data

    def _mock_response(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if endpoint == "/create_app":
            app_id = uuid4().hex
            self._mock_apps[app_id] = {"status": "ok", "application_id": app_id, "data": {}, "photo_ids": []}
            return {"status": "ok", "application_id": app_id}
        if endpoint == "/set_app":
            app_id = str(payload.get("application_id"))
            self._mock_apps.setdefault(app_id, {"application_id": app_id, "photo_ids": []})
            self._mock_apps[app_id]["data"] = payload.get("data") or {}
            return {"status": "ok"}
        if endpoint == "/get_app":
            app_id = str(payload.get("application_id"))
            return {"status": "ok", **self._mock_apps.get(app_id, {"application_id": app_id, "data": {}, "photo_ids": []})}
        if endpoint == "/delete_app":
            self._mock_apps.pop(str(payload.get("application_id")), None)
            return {"status": "ok"}
        if endpoint == "/add_app_photo":
            app_id = str(payload.get("application_id"))
            self._mock_apps.setdefault(app_id, {"application_id": app_id, "data": {}, "photo_ids": []})
            self._mock_apps[app_id].setdefault("photo_ids", []).append(uuid4().hex)
            return {"status": "ok"}
        if endpoint == "/clear_app_photo":
            app = self._mock_apps.get(str(payload.get("application_id")))
            if app:
                app["photo_ids"] = []
            return {"status": "ok"}
        if endpoint == "/get_jobs":
            return {
                "status": "ok",
                "jobs": [
                    {
                        "id": "mock-job",
                        "object": "Тестовый объект",
                        "position": "Тестовая должность",
                        "удаленный_подбор": True,
                    }
                ],
            }
        if endpoint == "/get_user_apps":
            apps = [
                app for app in self._mock_apps.values()
                if app.get("data", {}).get("user_id") == payload.get("tg_id")
            ]
            return {"status": "ok", "applications": apps}
        if endpoint == "/get_facility_binds":
            return {
                "status": "ok",
                "binds": [
                    {"facility": "Тестовый объект", "name": "Тестовый Пользователь"},
                ],
            }
        if endpoint == "/get_app_photo":
            return {"status": "ok", "pdf_url": ""}
        return {"status": "ok"}
