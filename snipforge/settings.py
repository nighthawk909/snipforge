"""The one place SnipForge's settings are opened.

Normally this is the registry (HKCU\\Software\\SnipForge\\SnipForge). Going through
QSettings.defaultFormat() lets tests redirect everything to a throwaway file.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings

ORGANIZATION = APPLICATION = "SnipForge"


def app_settings() -> QSettings:
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, ORGANIZATION, APPLICATION)
