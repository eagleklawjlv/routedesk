"""Dark desktop theme."""

from __future__ import annotations

from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtCore import Qt

STYLESHEET = """
QWidget {
  background: #121418;
  color: #e7e2d6;
  font-family: "Segoe UI", "Noto Sans", "Liberation Sans", sans-serif;
  font-size: 13px;
}
QMainWindow, QDialog, QWizard, QWizardPage { background: #121418; }
QFrame#panel {
  background: #181c22;
  border: 1px solid #2c3340;
  border-radius: 8px;
}
QWidget#panelInner { background: #181c22; }
QLabel#section {
  color: #9aa3b2;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  background: transparent;
}
QLabel#demoBanner {
  background: #e0a045;
  color: #1a1206;
  font-weight: 700;
  padding: 8px 12px;
  border-radius: 6px;
}
QLabel#liveBanner {
  background: #1b3330;
  color: #c6efe2;
  padding: 8px 12px;
  border-radius: 6px;
}
QLabel#notice {
  color: #c5bfb2;
  background: transparent;
}
QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QTableWidget, QSpinBox {
  background: #101318;
  color: #e7e2d6;
  border: 1px solid #343c4a;
  border-radius: 6px;
  padding: 4px 6px;
  selection-background-color: #6d4c16;
  selection-color: #fff8ec;
}
QHeaderView::section {
  background: #1c2129;
  color: #9aa3b2;
  border: none;
  padding: 6px;
}
QPushButton {
  background: #262c36;
  color: #e7e2d6;
  border: 1px solid #3c4554;
  border-radius: 6px;
  padding: 6px 12px;
}
QPushButton:hover { background: #323a46; }
QPushButton:disabled { color: #6d7582; background: #1b2028; }
QPushButton#primary {
  background: #c9842a;
  color: #1a1206;
  border: 1px solid #e0a45a;
  font-weight: 700;
}
QPushButton#primary:hover { background: #ddab55; }
QPushButton#primary:disabled { background: #5c4a2e; color: #b7a48a; }
QStatusBar { background: #0e1116; color: #9aa3b2; }
QSplitter::handle { background: #121418; }
QRadioButton, QCheckBox { background: transparent; spacing: 6px; }
QScrollBar:vertical { background: #121418; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #3a4250; min-height: 24px; border-radius: 4px; }
QScrollBar:horizontal { background: #121418; height: 12px; }
QScrollBar::handle:horizontal { background: #3a4250; min-width: 24px; border-radius: 4px; }
QMenuBar, QMenu { background: #181c22; color: #e7e2d6; }
QMenu::item:selected { background: #3d3424; }
QTableWidget { gridline-color: #2a3140; alternate-background-color: #161b22; }
"""

BAND_COLORS = {
    "high": "#6cb6ff",
    "low": "#e0a045",
    "null": "#e07070",
    "wormhole": "#c792ea",
    "unknown": "#9aa3b2",
}

STATUS_COLORS = {
    "applied": "#8dcea0",
    "failed": "#e07a7a",
    "uncertain": "#e0a045",
    "cancelled": "#8b93a1",
    "pending": "#d7d2c8",
}


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#121418"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e7e2d6"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#101318"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1a1e24"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e7e2d6"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#262c36"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e7e2d6"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#8a5a16"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#fff8ec"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1a1e24"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#e7e2d6"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8b93a1"))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)


def app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor("#121418"))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#e0a045"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(8, 8, 48, 48, 10, 10)
    painter.end()
    return QIcon(pix)
