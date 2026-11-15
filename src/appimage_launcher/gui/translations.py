"""Translation support for AppImage Launcher GUI.

Provides gettext-based translation with Qt integration.
"""

from __future__ import annotations

import gettext
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QLocale, QTranslator

# Translation domain
DOMAIN = "appimage-launcher"


# Translation directory (relative to package)
def get_translations_dir() -> Path:
    """Get the translations directory."""
    import appimage_launcher

    return Path(appimage_launcher.__file__).parent / "gui" / "translations"


# Global translator instances
_qt_translator: QTranslator | None = None
_gettext_translator = None


def setup_translations() -> None:
    """Set up translations for the application."""
    global _qt_translator, _gettext_translator

    # Get system locale
    system_locale = QLocale.system().name()  # e.g., "en_US", "de_DE"
    language = system_locale.split("_")[0]  # e.g., "en", "de"

    # Try to load Qt translations
    _qt_translator = QTranslator()
    translations_dir = get_translations_dir()

    # Load Qt translation
    qt_loaded = _qt_translator.load(
        f"qt_{system_locale}", str(translations_dir)
    ) or _qt_translator.load(f"qt_{language}", str(translations_dir))

    if qt_loaded:
        QCoreApplication.installTranslator(_qt_translator)

    # Load app translations
    app_translator = QTranslator()
    app_loaded = app_translator.load(
        f"{DOMAIN}_{system_locale}", str(translations_dir)
    ) or app_translator.load(f"{DOMAIN}_{language}", str(translations_dir))

    if app_loaded:
        QCoreApplication.installTranslator(app_translator)

    # Set up gettext for Python strings
    try:
        _gettext_translator = gettext.translation(
            DOMAIN,
            localedir=str(translations_dir),
            languages=[system_locale, language],
            fallback=True,
        )
        _gettext_translator.install()
    except FileNotFoundError:
        # No translations found, use fallback (English)
        _gettext_translator = gettext.NullTranslations()
        _gettext_translator.install()


def tr(source_text: str, disambiguation: str | None = None, n: int = -1) -> str:
    """Translate a string using Qt's translation system.

    This is a wrapper around QCoreApplication.translate for use in Python code.
    """
    return QCoreApplication.translate("Dialogs", source_text, disambiguation, n)


def _(message: str) -> str:
    """Gettext translation function for Python strings."""
    if _gettext_translator:
        return _gettext_translator.gettext(message)
    return message


def ngettext(singular: str, plural: str, n: int) -> str:
    """Gettext plural translation function."""
    if _gettext_translator:
        return _gettext_translator.ngettext(singular, plural, n)
    return singular if n == 1 else plural


# Initialize translations on module import
setup_translations()
