import logging

from mmpy_bot import Bot, Settings, ExamplePlugin

from bot.config import get_settings
from bot.logging import configure_logging
from bot.plugins import BasePlugin


logger = logging.getLogger(__name__)


def build_bot() -> Bot:
    settings = get_settings()

    return Bot(
        settings=Settings(), # No need to specify Mattermost settings here, as they are read directly from the environment variables
        plugins=[BasePlugin(settings=settings)],
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info("Starting Mattermost bot...")

    bot = build_bot()
    bot.run()


if __name__ == "__main__":
    main()
