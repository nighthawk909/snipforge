"""Turns a Theme into the Qt palette and the one app-wide stylesheet.

Windows never call setStyleSheet themselves; they tag widgets instead:
- buttons: setProperty("variant", "primary" | "ghost")
- labels: setProperty("sfRole", "muted" | "secondary" | "error" | "title" | "section")
- command bars use objectName "CommandBar" and follow the header band when a theme pack sets one.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from .tokens import Theme

WINDOWS_UI_FAMILIES = ("Segoe UI Variable Text", "Segoe UI")  # Windows 11's, then Windows 10's


def ui_font() -> QFont:
    """The app's font. One family, the first of WINDOWS_UI_FAMILIES that's installed: a font given
    a list of families makes Qt look up every fallback font the first time it's used (0.3 s on a
    laptop, seconds on GitHub's Windows runner), where one family looks for a fallback only when
    text has a character it lacks. Call it once the QApplication exists: Qt's font database
    crashes without one."""
    font = QFont()
    if sys.platform == "win32":
        font.setFamily(next((f for f in WINDOWS_UI_FAMILIES if QFontDatabase.hasFamily(f)), WINDOWS_UI_FAMILIES[-1]))
    font.setPointSizeF(10)
    return font


def build_palette(theme: Theme) -> QPalette:
    p, a = theme.palette, theme.accent
    pal = QPalette()
    roles = {
        QPalette.Window: p.bg, QPalette.WindowText: p.text, QPalette.Base: p.surface,
        QPalette.AlternateBase: p.surface2, QPalette.Text: p.text, QPalette.Button: p.surface2,
        QPalette.ButtonText: p.text, QPalette.BrightText: p.danger, QPalette.Highlight: a.fill,
        QPalette.HighlightedText: a.on, QPalette.ToolTipBase: p.surface, QPalette.ToolTipText: p.text,
        QPalette.PlaceholderText: p.text3, QPalette.Link: a.text, QPalette.LinkVisited: a.text,
        QPalette.Light: p.surface, QPalette.Midlight: p.surface2, QPalette.Mid: p.border,
        QPalette.Dark: p.border, QPalette.Shadow: "#000000", QPalette.Accent: a.fill,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(p.off))
    return pal


def _url(path: str) -> str:
    """Quoted, so profile names with apostrophes or accented letters still parse."""
    return '"' + path.replace("\\", "/").replace('"', "%22") + '"'


def build_stylesheet(theme: Theme, assets: Mapping[str, str] | None = None) -> str:
    """`assets` maps 'check' to a small image file; without it checkboxes show a filled square."""
    p, a, h = theme.palette, theme.accent, theme.header
    assets = assets or {}
    check = f"image: url({_url(assets['check'])});" if "check" in assets else ""
    up = f"image: url({_url(assets['up'])});" if "up" in assets else ""
    down = f"image: url({_url(assets['down'])});" if "down" in assets else ""
    return f"""
QWidget {{ color: {p.text}; }}
QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled, QToolButton:disabled {{ color: {p.off}; }}
QMainWindow, QDialog {{ background: {p.bg}; }}
QWidget[sfRole="surface"] {{ background: {p.surface}; }}
QLabel {{ background: transparent; }}
QLabel[sfRole="muted"] {{ color: {p.text3}; }}
QLabel[sfRole="secondary"] {{ color: {p.text2}; }}
QLabel[sfRole="error"] {{ color: {p.danger}; }}
QLabel[sfRole="title"] {{ font-size: 20px; font-weight: 600; }}
QLabel[sfRole="section"] {{ color: {p.text3}; font-size: 11px; font-weight: 600; }}

QPushButton {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border};
               border-radius: 6px; padding: 5px 12px; }}
QPushButton:hover {{ background: {p.hover}; }}
QPushButton:pressed {{ background: {p.select}; }}
QPushButton:focus {{ border-color: {a.fill}; }}
QPushButton:disabled {{ color: {p.off}; }}
QPushButton[variant="primary"] {{ background: {a.fill}; color: {a.on}; border-color: {a.fill}; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {a.fill_hover}; border-color: {a.fill_hover}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.surface2}; color: {p.off}; border-color: {p.border}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {p.hover}; }}

QToolButton {{
    background: transparent; color: {p.text}; border: 1px solid transparent; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {p.hover}; }}
QToolButton:pressed {{ background: {p.select}; }}
QToolButton:checked {{ background: {a.soft}; color: {a.text}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; border-radius: 6px;
    padding: 4px 8px; selection-background-color: {a.fill}; selection-color: {a.on}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {a.fill}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {p.off}; background: {p.surface2}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {p.hover}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ width: 10px; height: 10px; {up} }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ width: 10px; height: 10px; {down} }}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 22px; border: none; }}
QComboBox::down-arrow {{ width: 10px; height: 10px; {down} }}
QComboBox QAbstractItemView {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    selection-background-color: {p.hover}; selection-color: {p.text}; outline: none; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {p.text3}; border-radius: 4px; background: {p.surface}; }}
QCheckBox::indicator:hover {{ border-color: {p.text2}; }}
QCheckBox::indicator:checked {{ background: {a.fill}; border-color: {a.fill}; {check} }}
QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {p.text3}; border-radius: 9px; background: {p.surface}; }}
QRadioButton::indicator:checked {{
    width: 8px; height: 8px; border: 5px solid {a.fill}; border-radius: 9px; background: {a.on}; }}

QMenu {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; background: transparent; }}
QMenu::item:selected {{ background: {p.hover}; }}
QMenu::item:disabled {{ color: {p.off}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}

QToolTip {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; border-radius: 4px;
    padding: 4px 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {p.border}; border-radius: 3px; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 32px; }}
QScrollBar::handle:horizontal {{ min-width: 32px; }}
QScrollBar::handle:hover {{ background: {p.text3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {a.fill}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -6px 0; border-radius: 8px;
                              background: {p.surface}; border: 2px solid {a.fill}; }}

QListView, QListWidget, QTreeView {{ background: transparent; border: none; outline: none; }}
QListView::item {{ border-radius: 8px; padding: 6px; color: {p.text}; }}
QListView::item:hover {{ background: {p.hover}; }}
QListView::item:selected {{ background: {a.soft}; color: {a.text}; }}

QStatusBar {{ background: {p.surface}; color: {p.text2}; border-top: 1px solid {p.border}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{
    background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; border-radius: 4px;
    text-align: center; }}
QProgressBar::chunk {{ background: {a.fill}; border-radius: 3px; }}

QWidget#CommandBar {{ background: {h.bar}; border-bottom: 1px solid {h.border}; }}
#CommandBar QLabel {{ color: {h.text}; }}
#CommandBar QPushButton, #CommandBar QToolButton {{
    background: transparent; color: {h.text}; border: 1px solid transparent; }}
#CommandBar QPushButton:hover, #CommandBar QToolButton:hover {{ background: {h.hover}; }}
#CommandBar QPushButton[variant="outline"] {{ background: {h.button}; border-color: {h.button_border}; }}
#CommandBar QPushButton[variant="primary"] {{
    background: {h.primary_bg}; color: {h.primary_text}; border-color: {h.primary_bg}; }}

QLabel[sfRole="heading"] {{ font-size: 15px; font-weight: 600; }}
QWidget#Divider {{ background: {h.border}; }}

QWidget#ToolRail {{ background: {p.surface}; border: none; border-right: 1px solid {p.border}; }}
QWidget#ToolRail[side="right"] {{ border-right: none; border-left: 1px solid {p.border}; }}
#ToolRail QToolButton {{ border-radius: 8px; padding: 7px; }}
#ToolRail QToolButton#RailCustomize {{ color: {p.text2}; }}
QWidget#MiniToolbar {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
QToolButton#MiniButton {{ border-radius: 6px; padding: 0; }}
QToolButton#LayerToggle {{ background: transparent; border: none; border-radius: 4px; padding: 0;
    min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }}
QToolButton#LayerToggle:hover {{ background: {p.surface2}; }}
QListWidget#LayerList::item {{ padding: 0; }}
QLineEdit#HexColor {{ padding: 2px 6px; }}
QWidget#RailDivider {{ background: {p.border}; }}
QDialog#CommandPalette {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
QDialog#CommandPalette QLineEdit#CommandSearch {{ font-size: 14px; padding: 8px 10px; }}
QDialog#CommandPalette QListWidget#CommandList::item {{ padding: 6px 8px; }}
QListWidget#ToolList {{ border: 1px solid {p.border}; border-radius: 8px; padding: 4px; }}
QListWidget#ToolList::item {{ padding: 5px 6px; }}
QListWidget#ToolList::indicator {{
    width: 16px; height: 16px; border: 1px solid {p.text3}; border-radius: 4px; background: {p.surface}; }}
QListWidget#ToolList::indicator:checked {{ background: {a.fill}; border-color: {a.fill}; {check} }}

QScrollArea#PropertiesPanel {{ background: {p.surface}; border: none; border-left: 1px solid {p.border}; }}
QWidget#PanelBody {{ background: {p.surface}; }}
QWidget#PanelSection {{ background: transparent; border-top: 1px solid {p.border}; }}
QLabel#Chip {{ background: {a.soft}; color: {a.text}; border-radius: 9px; padding: 1px 8px; font-size: 11px;
    font-weight: 600; }}
QToolButton#StampChoice {{ border-radius: 6px; padding: 2px; }}
QToolButton#FormatToggle {{ border-radius: 6px; padding: 3px; }}
QToolButton#FormatToggle:checked {{ background: {a.soft}; border: 1px solid {a.fill}; }}
QToolButton#StampChoice:checked {{ background: {a.soft}; border: 1px solid {a.fill}; }}
QToolButton#CustomColor {{ border: 1px dashed {p.text3}; border-radius: 12px; padding: 3px; }}
QToolButton#PickColor {{ border: 1px solid {p.border}; border-radius: 12px; padding: 3px; }}
QToolButton#PickColor:checked {{ background: {a.soft}; border-color: {a.fill}; }}
QToolButton#ImageAction {{ border: 1px solid {p.border}; border-radius: 8px; padding: 6px; }}

QWidget#Segmented {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px; }}
#Segmented QToolButton {{ border-radius: 6px; padding: 4px 12px; color: {p.text2}; }}
#Segmented QToolButton:checked {{ background: {p.surface}; color: {p.text}; font-weight: 600;
    border: 1px solid {p.border}; }}

QWidget#Sidebar {{ background: {h.bg if h.branded else p.bg}; border-right: 1px solid {h.border}; }}
#Sidebar QLabel[sfRole="section"] {{ color: {h.text2}; }}
#Sidebar QPushButton[variant="primary"] {{ background: {h.primary_bg}; color: {h.primary_text};
    border-color: {h.primary_bg}; }}
QListView#SnipGrid {{ background: transparent; }}
QToolButton#Chip {{ background: {p.surface}; color: {p.text2}; border: 1px solid {p.border}; border-radius: 14px;
    padding: 4px 12px; }}
QToolButton#Chip:hover {{ border-color: {p.text3}; }}
QToolButton#Chip:checked {{ background: {a.soft}; color: {a.text}; border-color: {a.fill}; font-weight: 600; }}

QFrame#CaptureToolbar {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
#CaptureToolbar QToolButton {{ border-radius: 8px; padding: 6px 10px; color: {p.text2}; }}
#CaptureToolbar QToolButton:checked {{ background: {a.soft}; color: {a.text}; font-weight: 600; }}
#CaptureToolbar QToolButton:focus {{ border: 1px solid {a.fill}; }}
QToolButton#DelayButton::menu-indicator, QToolButton#SizeButton::menu-indicator {{ image: none; width: 0px; }}
QFrame#Countdown {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
QLabel#CountdownText {{ font-weight: 600; padding-right: 6px; }}
#Countdown QToolButton {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 5px 12px; }}
#Countdown QToolButton:hover {{ background: {p.hover}; }}
QFrame#CaptureToolbarSeparator {{ background: {p.border}; }}
QFrame#RecordPanel, QFrame#RecordBar {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
#RecordPanel QToolButton, #RecordBar QToolButton {{ border-radius: 8px; padding: 6px 10px; color: {p.text2}; }}
#RecordPanel QToolButton:checked {{ background: {a.soft}; color: {a.text}; font-weight: 600; }}
QPushButton#RecordStart {{ background: {p.danger}; color: #ffffff; border: none; border-radius: 8px;
    padding: 6px 16px; font-weight: 600; }}
QLabel#RecordProblem {{ color: {p.danger}; padding: 2px 6px; }}
QLabel#RecordTime {{ font-weight: 600; padding: 0 4px; }}
QLabel#RecordDot {{ color: {p.danger}; }}

QWidget#SettingsNav {{ background: {p.bg}; border-right: 1px solid {p.border}; }}
QWidget#SettingsBody, QScrollArea#SettingsScroll {{ background: {p.bg}; border: none; }}
QFrame#SettingCard {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; }}
QLabel#SettingTitle {{ font-weight: 600; }}
QLabel#StatusBadge {{ background: {a.soft}; border-radius: 22px; }}
QPushButton#AccentChoice {{ text-align: left; padding: 8px 12px; }}
QPushButton#AccentChoice:checked {{ background: {a.soft}; border-color: {a.fill}; color: {a.text}; font-weight: 600; }}

QWidget#Tray {{ background: {p.surface}; border-top: 1px solid {p.border}; }}
QStatusBar#TrayStatus {{ background: transparent; border: none; color: {p.text2}; }}
QScrollArea#TrayThumbs, QScrollArea#TrayThumbs > QWidget > QWidget {{ background: transparent; border: none; }}
#Thumb {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 6px; padding: 2px; }}
#Thumb:hover {{ border-color: {a.fill}; }}
#ZoomLabel {{ color: {h.text2}; }}
""" + _focus_rules(theme) + (_contrast_rules(theme) if theme.contrast else "") + _focus_on_accent(theme)


def _focus_rules(theme: Theme) -> str:
    """Where the keyboard is, on every control it can reach: an accent border. Repeated with the
    selectors above that set their own borders (an ID outranks a pseudo-state in Qt stylesheets)."""
    a = theme.accent
    return f"""
QCheckBox, QRadioButton {{ border: 1px solid transparent; border-radius: 4px; }}
QToolButton:focus, QPushButton:focus, QCheckBox:focus, QRadioButton:focus,
#CommandBar QToolButton:focus, #CommandBar QPushButton:focus, #ToolRail QToolButton:focus,
#Segmented QToolButton:focus, #Sidebar QPushButton:focus, QToolButton#Chip:focus, QToolButton#StampChoice:focus,
QToolButton#PickColor:focus, QToolButton#CustomColor:focus, QToolButton#ImageAction:focus,
QPushButton#AccentChoice:focus, #RecordPanel QToolButton:focus, #RecordBar QToolButton:focus,
#Countdown QToolButton:focus {{ border: 1px solid {a.fill}; }}
QListView:focus, QListWidget:focus, QTreeView:focus {{ border: 1px solid {a.fill}; border-radius: 6px; }}
QListView#SnipGrid:focus {{ border: none; }}
QListView::item:focus {{ border: 1px solid {a.fill}; }}
QSlider:focus {{ border: 1px solid {a.fill}; border-radius: 4px; }}
"""


def _focus_on_accent(theme: Theme) -> str:
    """Controls already filled or edged with the accent (primary buttons, chosen options; in high
    contrast every checked control) show the keyboard in a color that stands out from it. Last, so
    these win over the rules above."""
    p, a, h = theme.palette, theme.accent, theme.header
    checked_in_contrast = f"""
QToolButton:checked:focus, #ToolRail QToolButton:checked:focus, #Segmented QToolButton:checked:focus,
#CaptureToolbar QToolButton:checked:focus, #RecordPanel QToolButton:checked:focus,
QToolButton#Chip:checked:focus, QPushButton#AccentChoice:checked:focus {{ border: 1px solid {a.on}; }}
""" if theme.contrast else ""
    return f"""
QPushButton[variant="primary"]:focus {{ border: 1px solid {a.on}; }}
#CommandBar QPushButton[variant="primary"]:focus, #Sidebar QPushButton[variant="primary"]:focus {{
    border: 1px solid {h.primary_text}; }}
QPushButton#RecordStart:focus {{ border: 1px solid {p.bg}; }}  /* on its red (its text color in contrast) */
QPushButton#AccentChoice:checked:focus, QToolButton#Chip:checked:focus, QToolButton#StampChoice:checked:focus,
QToolButton#PickColor:checked:focus {{ border: 1px solid {p.text}; }}
""" + checked_in_contrast


def _contrast_rules(theme: Theme) -> str:
    """High contrast: the system's highlight marks what's hovered, checked or selected (its shades
    are all one color in a contrast theme), and borders are the text color."""
    p, a = theme.palette, theme.accent
    return f"""
QPushButton:hover, QToolButton:hover, #CommandBar QPushButton:hover, #CommandBar QToolButton:hover,
QToolButton#Chip:hover, #Thumb:hover {{ border: 1px solid {a.fill}; }}
QToolButton:checked, #Segmented QToolButton:checked, #CaptureToolbar QToolButton:checked,
#RecordPanel QToolButton:checked, QToolButton#Chip:checked, QPushButton#AccentChoice:checked {{
    background: {a.fill}; color: {a.on}; border: 1px solid {a.fill}; }}
QMenu::item:selected, QListView::item:selected, QListView::item:hover,
QComboBox QAbstractItemView {{ selection-background-color: {a.fill}; selection-color: {a.on}; }}
QMenu::item:selected, QListView::item:selected {{ background: {a.fill}; color: {a.on}; }}
QListView::item:hover {{ border: 1px solid {a.fill}; }}
QCheckBox::indicator, QRadioButton::indicator {{ border-color: {p.text}; }}
QScrollBar::handle {{ background: {p.text}; }}
QSlider::groove:horizontal {{ background: {p.text}; }}
"""
