import logging

import sublime

from .sgit import *  # noqa
from .sgit.git_extensions.legit import *  # noqa
from .sgit.git_extensions.git_flow import *  # noqa

LOG_FORMAT = "[%(asctime)s - %(levelname)-8s - %(name)s] %(message)s"

logger = logging.getLogger('SublimeGit')


def configure_logging(level_name):
    """Configure only the ``SublimeGit`` logger (never the root logger, which is
    shared with every other plugin in the host). Safe to call repeatedly: the
    stream handler is attached once and survives plugin reloads."""
    if not any(getattr(h, '_sublimegit', False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler._sublimegit = True
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(getattr(logging, (level_name or '').upper(), logging.WARNING))


def plugin_loaded():
    settings = sublime.load_settings('SublimeGit.sublime-settings')

    configure_logging(settings.get('log_level', ''))

    # Enable extensions (their commands check the module-level ``enabled`` flag)
    extensions = settings.get('git_extensions', {}) or {}
    git_extensions.legit.enabled = bool(extensions.get('legit', False))
    git_extensions.git_flow.enabled = bool(extensions.get('git_flow', False))


def plugin_unloaded():
    for handler in list(logger.handlers):
        if getattr(handler, '_sublimegit', False):
            logger.removeHandler(handler)
            handler.close()
