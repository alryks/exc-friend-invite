import logging

from mmpy_bot import Bot, Settings

from bot.config import get_settings
from bot.logging import configure_logging
from bot.plugins import FriendInvitePlugin


logger = logging.getLogger(__name__)


def build_bot() -> Bot:
    settings = get_settings()

    return Bot(
        settings=Settings(),
        plugins=[FriendInvitePlugin(settings=settings)],
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info("Starting Mattermost bot...")

    bot = build_bot()
    bot.run()


if __name__ == "__main__":
    main()
