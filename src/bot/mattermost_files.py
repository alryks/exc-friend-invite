from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/heic"}


@dataclass
class MattermostFile:
    file_id: str
    name: str
    mime_type: str
    size: int
    content: bytes


class MattermostFileError(Exception):
    pass


class MattermostFileClient:
    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def get_file(self, file_id: str) -> MattermostFile:
        info = self._get_info(file_id)
        content = self._download(file_id)
        return MattermostFile(
            file_id=file_id,
            name=str(info.get("name") or info.get("filename") or file_id),
            mime_type=str(info.get("mime_type") or info.get("mime") or ""),
            size=int(info.get("size") or len(content)),
            content=content,
        )

    def _get_info(self, file_id: str) -> dict[str, Any]:
        if hasattr(self._driver, "files"):
            return self._driver.files.get_file_info(file_id)
        client = self._driver.client
        return client.get(f"/api/v4/files/{file_id}/info")

    def _download(self, file_id: str) -> bytes:
        if hasattr(self._driver, "files"):
            data = self._driver.files.get_file(file_id)
        else:
            data = self._driver.client.get(f"/api/v4/files/{file_id}")
        if isinstance(data, bytes):
            return data
        if hasattr(data, "content"):
            return data.content
        if isinstance(data, str):
            return data.encode()
        raise MattermostFileError("Could not download Mattermost file")
