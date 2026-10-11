"""Daylight theme: soft cool-grey window, white floating panels, violet-to-blue accent."""
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

"""Centralized StudyPDF light/dark theme tokens and Qt stylesheet."""

THEME_MODE = "light"
THEME_NAME = "Daylight"

_PALETTES = {
    "light": {
        "BG0": "#f3f4f1", "BG1": "#fffefa", "BG2": "#f7f7f2", "BG3": "#e9eee9",
        "STAGE": "#e6eae6", "ACTIVE": "#dcece8", "BORDER": "#d8dfda", "TEXT": "#1e2c35",
        "MUTED": "#64737a", "ACCENT": "#176b66", "ACCENT_HOVER": "#25857c", "ACCENT2": "#527b9a",
        "INK": "#ffffff", "GOOD": "#16a34a", "WARN": "#d97706", "BAD": "#dc2626",
        "SCROLL": "#c6cbdb", "SCROLL_HOVER": "#aab1c6", "HERO1": "#ece8ff", "HERO2": "#e6eeff", "HERO3": "#e0f1ff",
    },
}

BG0 = BG1 = BG2 = BG3 = STAGE = ACTIVE = BORDER = TEXT = MUTED = ACCENT = ACCENT_HOVER = ACCENT2 = INK = GOOD = WARN = BAD = ""
GRADIENT = GRADIENT_HOVER = ""
STATUS_COLORS = {}
STATUS_LABELS = {"todo": "Not started", "progress": "In progress", "solved": "Solved", "difficult": "Difficult"}


def set_theme_mode(mode: str) -> str:
    """Set the global palette. Returns the normalized selected mode."""
    global THEME_MODE, THEME_NAME, BG0, BG1, BG2, BG3, STAGE, ACTIVE, BORDER, TEXT, MUTED
    global ACCENT, ACCENT_HOVER, ACCENT2, INK, GOOD, WARN, BAD, GRADIENT, GRADIENT_HOVER, STATUS_COLORS
    mode = "light"
    values = _PALETTES[mode]
    globals().update(values)
    THEME_MODE = mode
    THEME_NAME = "Paper"
    GRADIENT = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT2})"
    GRADIENT_HOVER = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT_HOVER}, stop:1 {ACCENT2})"
    STATUS_COLORS = {"todo": "#a3abbd", "progress": WARN, "solved": GOOD, "difficult": BAD}
    return mode


set_theme_mode("light")

_QSS = """
* { font-family: "Segoe UI Variable", "Segoe UI", "Noto Sans", sans-serif; font-size: 13px; color: @TEXT; }
QMainWindow, QDialog, QWidget#root { background: @BG0; }
QDialog { background: @BG1; }
QToolTip { background: @BG1; color: @TEXT; border: none; padding: 5px 9px; border-radius: 8px; }

QFrame#panel { background: @BG1; border: 1px solid @BORDER; border-radius: 18px; }
QFrame#topbar, QFrame#toolbar { background: transparent; border: none; }
QFrame#pill { background: @BG1; border: 1px solid @BORDER; border-radius: 18px; }
QWidget#stagebg { background: @STAGE; border: 1px solid @BORDER; border-radius: 20px; }
QFrame#dock { background: @BG1; border: 1px solid @BORDER; border-radius: 22px; }
QFrame#card { background: @BG2; border: 1px solid @BORDER; border-radius: 16px; }
QFrame#hero { border: 1px solid @BORDER; border-radius: 22px;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 @HERO1, stop:0.55 @HERO2, stop:1 @HERO3); }

QLabel { background: transparent; }
QLabel#h1 { font-size: 36px; font-weight: 700; }
QLabel#h2 { font-size: 15px; font-weight: 600; }
QLabel#muted { color: @MUTED; }
QLabel#section { color: @MUTED; font-size: 11px; font-weight: 600; }
QLabel#code { font-size: 40px; font-weight: 700; color: @ACCENT; background: @BG2; border: 1px dashed #b7b0ff; border-radius: 16px; padding: 12px; }

QPushButton { background: @BG1; border: 1px solid @BORDER; border-radius: 10px; padding: 8px 16px; }
QPushButton:hover { background: @BG3; border-color: #cdd2e1; }
QPushButton:disabled { color: #a9b0c2; background: @BG2; }
QPushButton[kind="primary"] { background: @GRADIENT; border: none; color: @INK; font-weight: 700; padding: 9px 20px; }
QPushButton[kind="primary"]:hover { background: @GRADIENT_HOVER; }
QPushButton[kind="danger"] { color: @BAD; }
QPushButton[kind="ghost"] { background: transparent; border-color: transparent; }
QPushButton[kind="ghost"]:hover { background: @BG3; }

QFrame#segment { background: @BG3; border: none; border-radius: 13px; }
QPushButton[kind="seg"] { background: transparent; border: none; border-radius: 10px; padding: 6px 15px; color: @MUTED; font-weight: 600; }
QPushButton[kind="seg"]:hover { color: @TEXT; background: transparent; }
QPushButton[kind="seg"]:checked { background: @BG1; color: @ACCENT; }
QDialog#palette { background: @BG1; border: 1px solid @BORDER; border-radius: 16px; }
QLineEdit#paletteinput { border: none; border-bottom: 1px solid @BORDER; border-radius: 0; padding: 14px 16px; font-size: 15px; background: transparent; }
QListWidget#palettelist::item { padding: 2px; margin: 1px 6px; border-radius: 9px; }
QListWidget#palettelist::item:selected { background: @ACTIVE; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 10px; padding: 5px; }
QToolButton:hover { background: @BG3; }
QToolButton:checked { background: @ACTIVE; border: 1px solid @ACCENT; }
QToolButton::menu-indicator { image: none; width: 0; }

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit { background: @BG1; border: 1px solid @BORDER; border-radius: 10px; padding: 8px 11px; selection-background-color: @ACCENT; selection-color: @INK; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus { border: 1px solid @ACCENT; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: @BG1; border: 1px solid @BORDER; selection-background-color: @BG3; outline: 0; }

QListWidget, QTextBrowser, QScrollArea { background: transparent; border: none; outline: 0; }
QListWidget::item { border-radius: 10px; padding: 2px; }
QListWidget::item:selected, QListWidget::item:hover { background: @BG3; }
QListWidget#pages::item:selected { background: @ACTIVE; border: 1px solid @ACCENT; }

QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: @MUTED; padding: 9px 11px; border-bottom: 2px solid transparent; font-weight: 600; }
QTabBar::tab:hover { color: @TEXT; }
QTabBar::tab:selected { color: @ACCENT; border-bottom: 2px solid @ACCENT; }

QMenu { background: @BG1; border: 1px solid @BORDER; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 8px 24px 8px 14px; border-radius: 8px; }
QMenu::item:selected { background: @BG3; }
QMenu::separator { height: 1px; background: @BORDER; margin: 5px 6px; }

QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 5px; border: 1px solid #b9c0d2; background: @BG1; }
QCheckBox::indicator:checked { background: @ACCENT; border-color: @ACCENT; }

QSlider::groove:horizontal { height: 4px; background: @BORDER; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @ACCENT; border-radius: 2px; }
QSlider::handle:horizontal { background: @TEXT; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QProgressBar { background: @BG2; border: none; border-radius: 4px; height: 8px; color: transparent; }
QProgressBar::chunk { background: @GRADIENT; border-radius: 4px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle { background: @SCROLL; border-radius: 4px; min-height: 30px; min-width: 30px; }
QScrollBar::handle:hover { background: @SCROLL_HOVER; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
"""


def build_qss() -> str:
    tokens = {"BG0": BG0, "BG1": BG1, "BG2": BG2, "BG3": BG3, "BORDER": BORDER, "TEXT": TEXT, "MUTED": MUTED,
              "ACCENT": ACCENT, "BAD": BAD, "INK": INK, "ACTIVE": ACTIVE, "STAGE": STAGE, "GRADIENT_HOVER": GRADIENT_HOVER, "GRADIENT": GRADIENT,
              "HERO1": _PALETTES[THEME_MODE]["HERO1"], "HERO2": _PALETTES[THEME_MODE]["HERO2"], "HERO3": _PALETTES[THEME_MODE]["HERO3"],
              "SCROLL": _PALETTES[THEME_MODE]["SCROLL"], "SCROLL_HOVER": _PALETTES[THEME_MODE]["SCROLL_HOVER"]}
    qss = _QSS
    for key in sorted(tokens, key=len, reverse=True):      # longest first so @GRADIENT_HOVER wins over @GRADIENT
        qss = qss.replace("@" + key, tokens[key])
    return qss


def apply_theme(app: QApplication, mode: str | None = None) -> None:
    if mode is not None:
        set_theme_mode(mode)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in ((QPalette.ColorRole.Window, BG0), (QPalette.ColorRole.Base, BG2), (QPalette.ColorRole.Text, TEXT),
                        (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Button, BG2),
                        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT),
                        (QPalette.ColorRole.HighlightedText, INK), (QPalette.ColorRole.ToolTipBase, BG3),
                        (QPalette.ColorRole.ToolTipText, TEXT)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    f = QFont("Segoe UI")
    f.setPixelSize(13)
    app.setFont(f)
    app.setStyleSheet(build_qss())
