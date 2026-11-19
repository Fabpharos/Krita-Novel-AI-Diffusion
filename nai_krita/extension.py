"""Krita extension: owns settings and the job queue, shared by every docker."""
import logging
import os

from krita import Extension, Krita  # type: ignore

from .qt import QStandardPaths, APP_DATA
from .settings import Settings
from .jobs import JobQueue

log = logging.getLogger("nai_krita")
_instance = None


def instance() -> "NovelAIExtension":
    return _instance


def settings_folder() -> str:
    # On Windows this is %APPDATA%\krita, next to the pykrita folder.
    return QStandardPaths.writableLocation(APP_DATA)


class NovelAIExtension(Extension):
    def __init__(self, parent):
        global _instance
        super().__init__(parent)
        _instance = self
        self.folder = settings_folder()
        try:
            self.settings = Settings.load(self.folder)
        except Exception as e:  # a broken settings file must not stop Krita
            log.error("Could not read NovelAI settings, using defaults: %s", e)
            self.settings = Settings()
        self.queue = JobQueue(lambda: self.settings)

    def save_settings(self):
        self.settings.save(self.folder)

    def setup(self):
        pass

    def createActions(self, window):
        pass
