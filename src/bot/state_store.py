from __future__ import annotations

from datetime import timedelta
from threading import RLock

from bot.models import FlowSession


class StateStore:
    def __init__(self, ttl_hours: int) -> None:
        self._ttl = timedelta(hours=ttl_hours)
        self._by_flow_id: dict[str, FlowSession] = {}
        self._by_user_id: dict[str, str] = {}
        self._lock = RLock()

    def save(self, session: FlowSession) -> FlowSession:
        with self._lock:
            self._purge_expired()
            session.touch()
            self._by_flow_id[session.flow_id] = session
            self._by_user_id[session.mattermost_user_id] = session.flow_id
            return session

    def get_by_flow_id(self, flow_id: str | None) -> FlowSession | None:
        if not flow_id:
            return None
        with self._lock:
            self._purge_expired()
            return self._by_flow_id.get(flow_id)

    def get_by_user_id(self, mattermost_user_id: str) -> FlowSession | None:
        with self._lock:
            self._purge_expired()
            flow_id = self._by_user_id.get(mattermost_user_id)
            if not flow_id:
                return None
            return self._by_flow_id.get(flow_id)

    def delete(self, flow_id: str | None) -> None:
        if not flow_id:
            return
        with self._lock:
            session = self._by_flow_id.pop(flow_id, None)
            if session:
                self._by_user_id.pop(session.mattermost_user_id, None)

    def _purge_expired(self) -> None:
        expired = [
            flow_id
            for flow_id, session in self._by_flow_id.items()
            if session.is_expired(self._ttl)
        ]
        for flow_id in expired:
            session = self._by_flow_id.pop(flow_id, None)
            if session:
                self._by_user_id.pop(session.mattermost_user_id, None)
