import json
import logging

from mmpy_bot import Message, Plugin, listen_to, listen_webhook, WebHookEvent, ActionEvent

from bot.config import Settings


logger = logging.getLogger(__name__)


class BasePlugin(Plugin):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.app_settings = settings

    def on_start(self) -> None:
        channel_id = self.app_settings.channel_id
        if not channel_id:
            logger.info("Startup channel is not configured, skipping startup message")
            return

        logger.info("Sending startup message to channel %s", channel_id)
        self.driver.create_post(
            channel_id=channel_id,
            message="Bot has started.",
        )

    def _open_example_dialog(self, event: ActionEvent) -> None:
        state = json.dumps(
            {
                "channel_id": event.channel_id,
                "user_id": event.user_id,
            }
        )
        dialog = {
            "trigger_id": event.trigger_id,
            "url": f"{self.app_settings.webhook_public_url}:{self.app_settings.webhook_public_port}/"
            "hooks/dialog-submit",
            "dialog": {
                "callback_id": "example-dialog",
                "title": "Example Dialog",
                "submit_label": "Create post",
                "state": state,
                "elements": [
                    {
                        "display_name": "Title",
                        "name": "title",
                        "type": "text",
                        "placeholder": "Weekly sync",
                    },
                    {
                        "display_name": "Summary",
                        "name": "summary",
                        "type": "textarea",
                        "placeholder": "What should be posted?",
                    },
                    {
                        "display_name": "Priority",
                        "name": "priority",
                        "type": "select",
                        "default": "medium",
                        "options": [
                            {"text": "Low", "value": "low"},
                            {"text": "Medium", "value": "medium"},
                            {"text": "High", "value": "high"},
                        ],
                    },
                ],
            },
        }
        self.driver.client.post("/api/v4/actions/dialogs/open", options=dialog)

    @listen_to("^ping$", needs_mention=True)
    async def ping(self, message: Message) -> None:
        self.driver.reply_to(message, "pong")

    @listen_to("^echo\\s+(.+)$", needs_mention=True)
    async def echo(self, message: Message, text: str) -> None:
        self.driver.reply_to(message, text)

    @listen_webhook("ping")
    @listen_webhook("pong")
    async def action_listener(self, event: WebHookEvent):
        self.driver.create_post(
            event.body["channel_id"], event.body["context"]["text"]
        )

    @listen_to("!button", direct_only=False)
    async def webhook_button(self, message: Message):
        """Creates a button that will trigger a webhook depending on the choice."""
        self.driver.reply_to(
            message,
            "",
            props={
                "attachments": [
                    {
                        "pretext": None,
                        "text": "Take your pick..",
                        "actions": [
                            {
                                "id": "sendPing",
                                "name": "Ping",
                                "integration": {
                                    "url": f"{self.app_settings.webhook_public_url}:{self.app_settings.webhook_public_port}/"
                                    "hooks/ping",
                                    "context": {
                                        "text": "The ping webhook works! :tada:",
                                    },
                                },
                            },
                            {
                                "id": "sendPong",
                                "name": "Pong",
                                "integration": {
                                    "url": f"{self.app_settings.webhook_public_url}:{self.app_settings.webhook_public_port}/"
                                    "hooks/pong",
                                    "context": {
                                        "text": "The pong webhook works! :tada:",
                                    },
                                },
                            },
                        ],
                    }
                ]
            },
        )

    @listen_to("^!dialog$", direct_only=False)
    async def dialog_example(self, message: Message) -> None:
        self.driver.reply_to(
            message,
            "Open the example dialog and submit it to create a post with the entered data.",
            props={
                "attachments": [
                    {
                        "text": "Example interactive dialog",
                        "actions": [
                            {
                                "id": "openExampleDialog",
                                "name": "Open dialog",
                                "integration": {
                                    "url": f"{self.app_settings.webhook_public_url}:{self.app_settings.webhook_public_port}/"
                                    "hooks/dialog-open",
                                },
                            }
                        ],
                    }
                ]
            },
        )

    @listen_webhook("dialog-open")
    async def open_dialog_webhook(self, event: ActionEvent) -> None:
        self._open_example_dialog(event)
        self.driver.respond_to_web(event, {})

    @listen_webhook("dialog-submit")
    async def submit_dialog_webhook(self, event: ActionEvent) -> None:
        submission = event.body.get("submission", {})
        state = json.loads(event.body.get("state") or "{}")
        channel_id = state.get("channel_id") or event.channel_id

        lines = [
            "New dialog submission:",
            f"- Title: {submission.get('title', '').strip() or '(empty)'}",
            f"- Summary: {submission.get('summary', '').strip() or '(empty)'}",
            f"- Priority: {submission.get('priority', '').strip() or '(empty)'}",
        ]
        self.driver.create_post(channel_id=channel_id, message="\n".join(lines))
        self.driver.respond_to_web(event, {})
