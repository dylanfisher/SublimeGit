# coding: utf-8
"""Test stub for the ``sublime_plugin`` module."""


class ApplicationCommand(object):
    pass


class WindowCommand(object):
    def __init__(self, window=None):
        self.window = window


class TextCommand(object):
    def __init__(self, view=None):
        self.view = view


class EventListener(object):
    pass


class ViewEventListener(object):
    def __init__(self, view=None):
        self.view = view
