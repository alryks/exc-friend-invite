from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from mmpy_bot import ActionEvent, Message, Plugin, listen_to, listen_webhook

from bot.config import Settings
from bot.formatting import (
    format_application_card,
    format_application_list_item,
    format_job,
    is_editable,
)
from bot.friend_api import FriendApiClient, FriendApiError
from bot.mattermost_files import MattermostFileClient, SUPPORTED_MIME_TYPES
from bot.models import FLOW_AWAITING_DOCUMENTS, FLOW_PREVIEW, FlowSession
from bot.state_store import StateStore
from bot.validators import date_api_to_input, normalize_ru_phone, parse_mm_date, validate_application


logger = logging.getLogger(__name__)
PAGE_SIZE = 5
FACILITY_BINDS_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class UserAccess:
    full_name: str
    facilities: set[str]


class FriendInvitePlugin(Plugin):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.app_settings = settings
        self.api = FriendApiClient(
            base_url=settings.friend_api_base_url,
            api_key=settings.friend_api_key,
            timeout_seconds=settings.friend_api_timeout_seconds,
            mock_mode=settings.friend_api_mock_mode,
        )
        self.state = StateStore(ttl_hours=settings.flow_ttl_hours)
        self._facility_binds_cache: tuple[datetime, list[dict[str, Any]]] | None = None

    @listen_to(r"^!start$", direct_only=True)
    async def start(self, message: Message) -> None:
        if not message.is_direct_message or message.root_id:
            return
        self._send_main_menu(message.channel_id)

    @listen_to(r".*", direct_only=True)
    async def document_listener(self, message: Message) -> None:
        if not message.is_direct_message or message.root_id or message.text == "!start":
            return
        session = self.state.get_by_user_id(message.user_id)
        if not session or session.state != FLOW_AWAITING_DOCUMENTS:
            return
        if not message.file_ids:
            self._post(
                message.channel_id,
                "**Загрузка документов**\n\nПрикрепите фото документов кандидата сообщениями в этот чат.",
                actions=self._document_actions(session),
            )
            return
        files = MattermostFileClient(self.driver)
        added = 0
        for file_id in message.file_ids:
            try:
                file = files.get_file(file_id)
                if file.size > self.app_settings.max_document_bytes:
                    self._post(
                        message.channel_id,
                        f"**Файл слишком большой.**\n\nМаксимальный размер: **{self._max_mb()} МБ**.",
                    )
                    continue
                if file.mime_type not in SUPPORTED_MIME_TYPES:
                    self._post(
                        message.channel_id,
                        "**Этот тип файла не поддерживается.**\n\nПрикрепите фото документа.",
                    )
                    continue
                self.api.add_app_photo(session.application_id or "", file.content)
                added += 1
                session.document_count += 1
                self.state.save(session)
            except FriendApiError:
                logger.exception("Failed to add application photo")
                self._post(message.channel_id, "**Не удалось связаться с сервисом анкет.**\n\nПопробуйте позже.")
            except Exception:
                logger.exception("Failed to process Mattermost file")
                self._post(message.channel_id, "**Не удалось обработать файл.**\n\nПопробуйте загрузить его еще раз.")
        if added:
            self._post(
                message.channel_id,
                f"**Документ добавлен.**\n\nЗагружено: **{session.document_count}**.",
                actions=self._document_actions(session),
            )

    @listen_webhook("friend-action")
    async def action(self, event: ActionEvent) -> None:
        context = event.context or event.body.get("context", {})
        action = context.get("action")
        try:
            if action == "main":
                self._send_main_menu(event.channel_id, update_post_id=event.post_id)
            elif action == "list":
                self._show_applications(
                    event.user_id,
                    event.channel_id,
                    int(context.get("page", 0)),
                    update_post_id=event.post_id,
                )
            elif action == "open":
                self._show_application(event.channel_id, event.user_id, context.get("application_id"))
            elif action == "add":
                self._open_application_dialog(event)
            elif action == "finish_upload":
                self._finish_upload(event)
            elif action == "clear_docs":
                self._clear_documents(event)
            elif action == "submit":
                self._submit_application(event)
            elif action == "reload_docs":
                self._reload_documents(event)
            elif action == "cancel":
                self._cancel_application(event)
            elif action == "edit":
                self._open_application_dialog(event, edit=True)
        except FriendApiError:
            logger.exception("Friend API request failed")
            update_post_id = None if action in {"add", "open"} else event.post_id
            self._post(
                event.channel_id,
                "**Не удалось связаться с сервисом анкет.**\n\nПопробуйте позже.",
                update_post_id=update_post_id,
            )
        finally:
            self.driver.respond_to_web(event, {})

    @listen_webhook("friend-dialog-submit")
    async def dialog_submit(self, event: ActionEvent) -> None:
        if event.body.get("cancelled"):
            self.driver.respond_to_web(event, {})
            return
        try:
            state = json.loads(event.body.get("state") or "{}")
            logger.debug(
                "Received application dialog submit: flow_id=%s user_id=%s channel_id=%s",
                state.get("flow_id"),
                event.body.get("user_id"),
                event.body.get("channel_id"),
            )
            session = self.state.get_by_flow_id(state.get("flow_id"))
            if not session:
                logger.debug("Dialog submit session not found: state=%s", state)
                self.driver.respond_to_web(event, {"error": "Сессия устарела. Начните заново командой !start."})
                return
            data, errors = self._submission_to_data(session, event.body.get("submission", {}))
            if errors:
                logger.info("Dialog submit validation errors: flow_id=%s errors=%s", session.flow_id, errors)
                self.driver.respond_to_web(event, {"errors": errors})
                return
            if not self._session_has_job_access(session, data.get("job")):
                self.driver.respond_to_web(event, {"error": "Выбранная вакансия вам недоступна."})
                return
            was_edit = bool(session.application_id)
            if was_edit:
                app = self.api.get_app(session.application_id)
                current = app.get("data") if isinstance(app.get("data"), dict) else app
                merged = {**current, **data}
                merged.pop("submitted", None)
            else:
                session.application_id = self.api.create_app()
                merged = data
            self.api.set_app(session.application_id, merged)
            session.state = FLOW_PREVIEW if was_edit else FLOW_AWAITING_DOCUMENTS
            self.state.save(session)
            if was_edit:
                self._post(
                    session.channel_id,
                    format_application_card({"data": merged}, session.document_count),
                    actions=[
                        self._button("Отправить анкету", "submit", flow_id=session.flow_id),
                        self._button("Редактировать", "edit", flow_id=session.flow_id),
                        self._button("Загрузить документы заново", "reload_docs", flow_id=session.flow_id),
                        self._button("Отменить", "cancel", flow_id=session.flow_id),
                    ],
                )
            else:
                self._post(
                    session.channel_id,
                    format_application_card({"data": merged}, session.document_count)
                    + "\n\n**Анкета создана.**\n\nПрикрепите фото документов кандидата сообщениями в этот чат.\n"
                    "Когда все документы загружены, нажмите \"Закончить загрузку\".",
                    actions=self._document_actions(session),
                )
            self.driver.respond_to_web(event, {})
        except FriendApiError:
            logger.exception("Dialog submit failed")
            self.driver.respond_to_web(event, {"error": "Не удалось связаться с сервисом анкет. Попробуйте позже."})
        except Exception:
            logger.exception("Unexpected dialog submit failure")
            self.driver.respond_to_web(event, {"error": "Не удалось сохранить анкету. Попробуйте позже."})

    def _send_main_menu(self, channel_id: str, update_post_id: str | None = None) -> None:
        self._post(
            channel_id,
            '### Акция "Приведи друга"\n\nВы можете добавить нового кандидата или посмотреть ранее отправленные анкеты.',
            actions=[
                self._button("Добавить кандидата", "add"),
                self._button("Список кандидатов", "list"),
            ],
            update_post_id=update_post_id,
        )

    def _show_applications(self, user_id: str, channel_id: str, page: int, update_post_id: str | None = None) -> None:
        apps = self.api.get_user_apps(_surrogate_user_id(user_id))
        apps.reverse()
        if not apps:
            self._post(
                channel_id,
                "**У вас пока нет отправленных кандидатов.**",
                actions=[self._button("Добавить кандидата", "add"), self._button("Назад", "main")],
                update_post_id=update_post_id,
            )
            return
        start = max(page, 0) * PAGE_SIZE
        chunk = apps[start : start + PAGE_SIZE]
        actions = [self._button("В главное меню", "main")]
        if page > 0:
            actions.append(self._button("Назад", "list", page=page - 1))
        actions.extend(
            self._button(
                str(start + index + 1),
                "open",
                application_id=str(app.get("application_id") or app.get("_id") or app.get("id")),
            )
            for index, app in enumerate(chunk)
        )
        if start + PAGE_SIZE < len(apps):
            actions.append(self._button("Далее", "list", page=page + 1))
        text = "\n\n".join(
            format_application_list_item(app, number=start + index + 1)
            for index, app in enumerate(chunk)
        )
        self._post(channel_id, text, actions=actions, update_post_id=update_post_id)

    def _show_application(self, channel_id: str, user_id: str, application_id: str | None) -> None:
        if not application_id:
            self._post(channel_id, "**Анкета не найдена.**")
            return
        app = self.api.get_app(application_id)
        text = format_application_card(app)
        photo = self.api.get_app_photo(application_id) if _has_documents(app) else {}
        if photo.get("pdf_url"):
            text += f"\n\n**Документы PDF:** {photo['pdf_url']}"
        actions = [self._button("В главное меню", "main")]
        if is_editable(app):
            session = FlowSession(
                mattermost_user_id=user_id,
                surrogate_user_id=_surrogate_user_id(user_id),
                channel_id=channel_id,
                application_id=application_id,
                state=FLOW_PREVIEW,
                enforce_facility_access=False,
            )
            self.state.save(session)
            actions = [
                self._button("Редактировать", "edit", flow_id=session.flow_id),
                self._button("Загрузить документы заново", "reload_docs", flow_id=session.flow_id),
                self._button("В главное меню", "main"),
            ]
        self._post(channel_id, text, actions=actions)

    def _open_application_dialog(self, event: ActionEvent, edit: bool = False) -> None:
        session = self.state.get_by_flow_id((event.context or {}).get("flow_id")) if edit else None
        if not session:
            session = FlowSession(
                mattermost_user_id=event.user_id,
                surrogate_user_id=_surrogate_user_id(event.user_id),
                channel_id=event.channel_id,
                team_id=event.team_id,
            )
        app_data: dict[str, Any] = {}
        if edit and session.application_id:
            app = self.api.get_app(session.application_id)
            app_data = app.get("data") if isinstance(app.get("data"), dict) else app
        jobs = _filter_jobs(self.api.get_jobs())
        if session.enforce_facility_access:
            access = self._authorize_user(event.user_id, event.channel_id, update_post_id=event.post_id)
            if access is None:
                return
            jobs = _filter_jobs_by_facilities(jobs, access.facilities)
        elif self.app_settings.enable_access_check:
            access = self._authorize_user(event.user_id)
            if access is None:
                current_job = app_data.get("job")
                jobs = [current_job] if isinstance(current_job, dict) else []
            else:
                jobs = _filter_jobs_by_facilities(jobs, access.facilities)
                current_job = app_data.get("job")
                if isinstance(current_job, dict) and current_job not in jobs:
                    jobs.insert(0, current_job)
        if not jobs:
            self._post(
                event.channel_id,
                "**Сейчас нет доступных вам вакансий для удаленного подбора.**",
                actions=[self._button("В главное меню", "main")],
                update_post_id=event.post_id,
            )
            return
        session.jobs = {str(i): job for i, job in enumerate(jobs)}
        self.state.save(session)
        logger.debug(
            "Opening application dialog: flow_id=%s user_id=%s channel_id=%s jobs=%s edit=%s",
            session.flow_id,
            event.user_id,
            event.channel_id,
            len(session.jobs),
            edit,
        )
        dialog = {
            "trigger_id": event.trigger_id,
            "url": self.app_settings.webhook_url("friend-dialog-submit"),
            "dialog": {
                "callback_id": "friend-application",
                "title": "Новый кандидат",
                "submit_label": "Сохранить",
                "state": json.dumps({"flow_id": session.flow_id}, ensure_ascii=False),
                "elements": self._dialog_elements(session, app_data),
            },
        }
        self.driver.integration_actions.open_interactive_dialog(dialog)

    def _finish_upload(self, event: ActionEvent) -> None:
        session = self._session_from_event(event)
        if not session or not session.application_id:
            self._post(event.channel_id, "**Сессия устарела.**\n\nНачните заново командой `!start`.", update_post_id=event.post_id)
            return
        app = self.api.get_app(session.application_id)
        if not _has_documents(app) and session.document_count <= 0:
            self._post(
                event.channel_id,
                "**Добавьте хотя бы один документ** перед отправкой анкеты.",
                actions=self._document_actions(session),
                update_post_id=event.post_id,
            )
            return
        session.state = FLOW_PREVIEW
        self.state.save(session)
        self._post(
            event.channel_id,
            format_application_card(app, session.document_count),
            actions=[
                self._button("Отправить анкету", "submit", flow_id=session.flow_id),
                self._button("Редактировать", "edit", flow_id=session.flow_id),
                self._button("Загрузить документы заново", "reload_docs", flow_id=session.flow_id),
                self._button("Отменить", "cancel", flow_id=session.flow_id),
            ],
            update_post_id=event.post_id,
        )

    def _clear_documents(self, event: ActionEvent) -> None:
        session = self._session_from_event(event)
        if session and session.application_id:
            self.api.clear_app_photo(session.application_id)
            session.document_count = 0
            self.state.save(session)
            self._post(
                event.channel_id,
                "**Документы очищены.**",
                actions=self._document_actions(session),
                update_post_id=event.post_id,
            )

    def _reload_documents(self, event: ActionEvent) -> None:
        session = self._session_from_event(event)
        if session and session.application_id:
            self.api.clear_app_photo(session.application_id)
            session.document_count = 0
            session.state = FLOW_AWAITING_DOCUMENTS
            self.state.save(session)
            self._post(
                event.channel_id,
                "**Загрузка документов**\n\nПрикрепите фото документов кандидата сообщениями в этот чат.",
                actions=self._document_actions(session),
                update_post_id=event.post_id,
            )

    def _submit_application(self, event: ActionEvent) -> None:
        session = self._session_from_event(event)
        if not session or not session.application_id:
            self._post(event.channel_id, "**Сессия устарела.**\n\nНачните заново командой `!start`.", update_post_id=event.post_id)
            return
        app = self.api.get_app(session.application_id)
        data = app.get("data") if isinstance(app.get("data"), dict) else app
        errors = validate_application(data)
        if errors:
            self._post(event.channel_id, "**Проверьте поля анкеты** перед отправкой.", update_post_id=event.post_id)
            return
        if not self._session_has_job_access(session, data.get("job"), event.channel_id, event.post_id):
            return
        if not _has_documents(app) and session.document_count <= 0:
            self._post(event.channel_id, "**Добавьте хотя бы один документ** перед отправкой анкеты.", update_post_id=event.post_id)
            return
        data = {**data, "user_id": session.surrogate_user_id, "submitted": True, "comment": data.get("comment") or ""}
        self.api.set_app(session.application_id, data)
        self.state.delete(session.flow_id)
        self._post(
            event.channel_id,
            "**Анкета отправлена.**",
            actions=[self._button("Добавить кандидата", "add"), self._button("Список кандидатов", "list")],
            update_post_id=event.post_id,
        )

    def _cancel_application(self, event: ActionEvent) -> None:
        session = self._session_from_event(event)
        if session and session.application_id:
            self.api.delete_app(session.application_id)
            self.state.delete(session.flow_id)
        self._send_main_menu(event.channel_id, update_post_id=event.post_id)

    def _submission_to_data(self, session: FlowSession, submission: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        phone = normalize_ru_phone(str(submission.get("phone") or ""))
        data = {
            "job": session.jobs.get(str(submission.get("job_id"))),
            "referral": str(submission.get("referral") or "").strip(),
            "name": str(submission.get("name") or "").strip(),
            "gender": submission.get("gender"),
            "phone": phone if phone is not None else submission.get("phone"),
            "age": _safe_date(submission.get("age")),
            "date_on_object": _safe_date(submission.get("date_on_object")),
            "residence": submission.get("residence"),
            "comment": str(submission.get("comment") or "").strip(),
            "user_id": session.surrogate_user_id,
        }
        return data, validate_application(data)

    def _dialog_elements(self, session: FlowSession, data: dict[str, Any]) -> list[dict[str, Any]]:
        current_job = data.get("job")
        default_job = next((key for key, job in session.jobs.items() if job == current_job), None)
        elements = [
            {
                "display_name": "Вакансия",
                "name": "job_id",
                "type": "select",
                "optional": False,
                "options": [{"text": format_job(job)[:75], "value": key} for key, job in session.jobs.items()],
            },
            {"display_name": "Рекомендатель", "name": "referral", "type": "text", "optional": False, "default": data.get("referral", "")},
            {"display_name": "ФИО кандидата", "name": "name", "type": "text", "optional": False, "default": data.get("name", "")},
            {
                "display_name": "Пол",
                "name": "gender",
                "type": "radio",
                "optional": False,
                "options": [{"text": "Мужской", "value": "Мужской"}, {"text": "Женский", "value": "Женский"}],
                "default": data.get("gender", "Мужской"),
            },
            {"display_name": "Телефон", "name": "phone", "type": "text", "subtype": "tel", "optional": True, "default": data.get("phone", "")},
            {
                "display_name": "Дата рождения",
                "name": "age",
                "type": "text",
                "optional": False,
                "placeholder": "ДД.ММ.ГГГГ",
                "default": date_api_to_input(data.get("age")),
            },
            {
                "display_name": "Дата прибытия",
                "name": "date_on_object",
                "type": "text",
                "optional": False,
                "placeholder": "ДД.ММ.ГГГГ",
                "default": date_api_to_input(data.get("date_on_object")),
            },
            {
                "display_name": "Гражданство",
                "name": "residence",
                "type": "select",
                "optional": False,
                "default": data.get("residence", "Россия"),
                "options": [{"text": value, "value": value} for value in ["Россия", "Беларусь", "Киргизия", "Казахстан"]],
            },
            {"display_name": "Комментарий", "name": "comment", "type": "textarea", "optional": True, "default": data.get("comment", "")},
        ]
        if default_job is not None:
            elements[0]["default"] = default_job
        return elements

    def _document_actions(self, session: FlowSession) -> list[dict[str, Any]]:
        return [
            self._button("Закончить загрузку", "finish_upload", flow_id=session.flow_id),
            self._button("Очистить документы", "clear_docs", flow_id=session.flow_id),
            self._button("Отменить анкету", "cancel", flow_id=session.flow_id),
        ]

    def _session_from_event(self, event: ActionEvent) -> FlowSession | None:
        context = event.context or event.body.get("context", {})
        return self.state.get_by_flow_id(context.get("flow_id")) or self.state.get_by_user_id(event.user_id)

    def _authorize_user(
        self,
        user_id: str,
        channel_id: str | None = None,
        update_post_id: str | None = None,
    ) -> UserAccess | None:
        if not self.app_settings.enable_access_check:
            return UserAccess(full_name="", facilities=set())
        try:
            full_name = self._mattermost_full_name(user_id)
        except Exception:
            logger.exception("Could not load Mattermost user info: user_id=%s", user_id)
            if channel_id:
                self._post(
                    channel_id,
                    "**Не удалось проверить доступ.**\n\nПопробуйте позже.",
                    actions=[self._button("В главное меню", "main")],
                    update_post_id=update_post_id,
                )
            return None
        normalized_user_name = _normalize_name(full_name)
        facilities = {
            str(bind.get("facility") or "").strip()
            for bind in self._get_facility_binds()
            if _names_match(normalized_user_name, _normalize_name(str(bind.get("name") or "")))
            and str(bind.get("facility") or "").strip()
        }
        if facilities:
            return UserAccess(full_name=full_name, facilities=facilities)
        logger.info("Mattermost user is not authorized: user_id=%s full_name=%r", user_id, full_name)
        if channel_id:
            self._post(
                channel_id,
                "**Доступ запрещен.**\n\nВаш пользователь Mattermost не найден в списке сотрудников, которым доступны вакансии.",
                actions=[self._button("В главное меню", "main")],
                update_post_id=update_post_id,
            )
        return None

    def _session_has_job_access(
        self,
        session: FlowSession,
        job: Any,
        channel_id: str | None = None,
        update_post_id: str | None = None,
    ) -> bool:
        if not self.app_settings.enable_access_check or not session.enforce_facility_access:
            return True
        access = self._authorize_user(session.mattermost_user_id, channel_id, update_post_id)
        if access is None:
            return False
        if isinstance(job, dict) and _normalize_facility(_job_facility(job)) in {_normalize_facility(f) for f in access.facilities}:
            return True
        if channel_id:
            self._post(
                channel_id,
                "**Выбранная вакансия вам недоступна.**",
                update_post_id=update_post_id,
            )
        return False

    def _get_facility_binds(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        if self._facility_binds_cache:
            cached_at, binds = self._facility_binds_cache
            if now - cached_at < FACILITY_BINDS_TTL:
                return binds
        binds = self.api.get_facility_binds()
        self._facility_binds_cache = (now, binds)
        return binds

    def _mattermost_full_name(self, user_id: str) -> str:
        if self.app_settings.access_check_debug_override and self.app_settings.access_check_debug_full_name.strip():
            full_name = self.app_settings.access_check_debug_full_name.strip()
            logger.warning("Using debug Mattermost full name override for access check: %r", full_name)
            return full_name
        user = self.driver.get_user_info(user_id)
        parts = [
            str(user.get("last_name") or "").strip(),
            str(user.get("first_name") or "").strip(),
        ]
        full_name = " ".join(part for part in parts if part)
        if full_name:
            return full_name
        for field in ("nickname", "username"):
            value = str(user.get(field) or "").strip()
            if value:
                return value
        return ""

    def _button(self, name: str, action: str, **context: Any) -> dict[str, Any]:
        return {
            "name": name,
            "integration": {
                "url": self.app_settings.webhook_url("friend-action"),
                "context": {"action": action, **context},
            },
        }

    def _post(
        self,
        channel_id: str,
        message: str,
        actions: list[dict[str, Any]] | None = None,
        update_post_id: str | None = None,
    ) -> None:
        props = None
        if actions:
            props = {"attachments": [{"text": message, "actions": actions}]}
            message = ""
        if update_post_id:
            try:
                self.driver.posts.patch_post(update_post_id, {"message": message, "props": props or {}})
                return
            except Exception:
                logger.exception("Failed to update Mattermost post, creating a new one")
        self.driver.create_post(channel_id=channel_id, message=message, props=props)

    def _max_mb(self) -> int:
        return max(1, self.app_settings.max_document_bytes // (1024 * 1024))


def _surrogate_user_id(mm_user_id: str) -> int:
    digest = hashlib.sha256(mm_user_id.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big") & ((1 << 63) - 1)


def _filter_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [job for job in jobs if job.get("удаленный_подбор", True) is True]


def _filter_jobs_by_facilities(jobs: list[dict[str, Any]], facilities: set[str]) -> list[dict[str, Any]]:
    if not facilities:
        return jobs
    normalized_facilities = {_normalize_facility(facility) for facility in facilities}
    return [job for job in jobs if _normalize_facility(_job_facility(job)) in normalized_facilities]


def _job_facility(job: dict[str, Any]) -> str:
    for key in ("объект", "object", "facility", "facility_name", "name_object"):
        value = job.get(key)
        if value:
            return str(value)
    return ""


def _normalize_facility(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("ё", "е").strip().casefold())


def _normalize_name(value: str) -> tuple[str, ...]:
    normalized = value.replace("ё", "е").casefold()
    return tuple(part for part in re.split(r"[\s\-]+", normalized) if part)


def _names_match(user_name: tuple[str, ...], bind_name: tuple[str, ...]) -> bool:
    if not user_name or not bind_name:
        return False
    if user_name == bind_name:
        return True
    return len(user_name) >= 2 and len(bind_name) >= 2 and user_name[:2] == bind_name[:2]


def _safe_date(value: Any) -> str:
    try:
        return parse_mm_date(str(value))
    except ValueError:
        return ""


def _has_documents(app: dict[str, Any]) -> bool:
    data = app.get("data") if isinstance(app.get("data"), dict) else app
    photo_ids = app.get("photo_ids") or data.get("photo_ids")
    return bool(photo_ids or app.get("photo_pdf") or data.get("photo_pdf"))
