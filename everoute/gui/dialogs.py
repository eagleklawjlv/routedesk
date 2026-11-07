"""SSO setup wizard and the about dialog."""

from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from everoute.account import normalize_callback, validate_client_id
from everoute.messages import ABOUT_TEXT, NO_SECRET_NOTE, SCOPE, SSO_SETUP_INTRO
from everoute.store import Settings


class IntroPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Register your own EVE application")
        layout = QVBoxLayout(self)
        text = QLabel(SSO_SETUP_INTRO)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(text)
        button = QPushButton("Open the EVE developers site")
        button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://developers.eveonline.com/applications"))
        )
        layout.addWidget(button)
        layout.addStretch(1)


class ClientPage(QWizardPage):
    def __init__(self, settings: Settings):
        super().__init__()
        self.setTitle("Client ID and loopback callback")
        self.settings = settings
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.client_id = QLineEdit(settings.client_id)
        self.client_id.setMaxLength(200)
        self.client_id.setPlaceholderText("Paste the client ID. Not a secret.")
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(settings.callback_port))
        self.host = QLineEdit(settings.callback_host)
        self.host.setMaxLength(32)
        self.callback = QLabel()
        self.callback.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.callback.setWordWrap(True)
        form.addRow("Client ID", self.client_id)
        form.addRow("Callback host", self.host)
        form.addRow("Callback port", self.port)
        form.addRow("Register this URL", self.callback)
        layout.addLayout(form)
        note = QLabel(
            "CCP must have this exact callback URL on the application. "
            "Host must be 127.0.0.1 or localhost. The listener always binds to 127.0.0.1. "
            f"Scope to enable on the application: {SCOPE}."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.client_id.textChanged.connect(self._refresh)
        self.port.valueChanged.connect(self._refresh)
        self.host.textChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        host = self.host.text().strip() or "127.0.0.1"
        self.callback.setText(f"http://{host}:{int(self.port.value())}/callback")

    def validatePage(self) -> bool:
        try:
            client_id = validate_client_id(self.client_id.text())
            host, port = normalize_callback(self.host.text(), int(self.port.value()), self.settings)
        except ValueError as exc:
            self.setSubTitle(str(exc))
            return False
        self.settings.client_id = client_id
        self.settings.callback_host = host
        self.settings.callback_port = port
        return True


class ConfirmPage(QWizardPage):
    def __init__(self, settings: Settings, keyring_ok: bool):
        super().__init__()
        self.setTitle("What will be stored")
        self.settings = settings
        self.file_box = QCheckBox(
            "The OS credential store is unavailable. Store refresh tokens in a private file "
            "under the app data folder. Anyone who can read that file can use the tokens."
        )
        self.file_box.setChecked(settings.allow_file_token_store)
        self.file_box.setVisible(not keyring_ok)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        secret = QLabel(NO_SECRET_NOTE)
        secret.setWordWrap(True)
        layout.addWidget(secret)
        layout.addWidget(self.file_box)
        if keyring_ok:
            ok = QLabel("Refresh tokens will use the operating system credential store.")
            ok.setWordWrap(True)
            layout.addWidget(ok)
        layout.addStretch(1)

    def initializePage(self) -> None:
        self.summary.setText(
            f"Client ID: {self.settings.client_id}\n"
            f"Callback: {self.settings.redirect_uri()}\n"
            f"Scope requested at login: {SCOPE}"
        )

    def validatePage(self) -> bool:
        self.settings.allow_file_token_store = self.file_box.isChecked()
        return True


class SetupWizard(QWizard):
    def __init__(self, settings: Settings, keyring_ok: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set up EVE SSO")
        self.setMinimumSize(680, 520)
        self.settings = settings
        self.addPage(IntroPage())
        self.addPage(ClientPage(settings))
        self.addPage(ConfirmPage(settings, keyring_ok))


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Route Desk")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        text = QPlainTextEdit(ABOUT_TEXT.strip())
        text.setReadOnly(True)
        text.setObjectName("aboutText")
        layout.addWidget(text)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
