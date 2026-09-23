# coding: utf-8
import importlib
import inspect
import pkgutil

import sublime_plugin

import sgit

PLUGIN_BASES = (sublime_plugin.ApplicationCommand, sublime_plugin.WindowCommand,
                sublime_plugin.TextCommand, sublime_plugin.EventListener,
                sublime_plugin.ViewEventListener)


def test_every_command_and_listener_is_exported():
    """Sublime only registers classes it finds in SublimeGit.py, which gets them
    via ``from .sgit import *``. A class missing from ``sgit/__init__.py`` is
    silently never registered, and ``run_command`` on it does nothing."""
    missing = []
    for info in pkgutil.iter_modules(sgit.__path__):
        module = importlib.import_module('sgit.' + info.name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (obj.__module__ == module.__name__ and issubclass(obj, PLUGIN_BASES)
                    and getattr(sgit, name, None) is not obj):
                missing.append('%s.%s' % (info.name, name))
    assert missing == []
