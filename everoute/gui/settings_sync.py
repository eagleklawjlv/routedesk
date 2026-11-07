"""One-shot copy of EVE character settings files, with a separate account opt-in."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from everoute.eve_settings import (
    COVERAGE_NOTE,
    INSTRUCTIONS,
    KIND_CHAR,
    KIND_USER,
    ClientsStillRunning,
    SettingsSyncError,
    apply_copies,
    describe_character,
    describe_plan,
    identify_profile,
    list_account_files,
    list_character_files,
    list_manifests,
    lookup_public_name,
    plan_copies,
    restore_manifest,
    windows_eve_client_running,
)
from everoute.http import HttpClient

BACKUP_DIRNAME = "settings-sync-backups"


class SettingsSyncDialog(QDialog):
    def __init__(
        self,
        data_dir: Path,
        library,
        *,
        client_running=None,
        http: HttpClient | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Copy EVE character settings")
        self.resize(980, 860)
        self.data_dir = Path(data_dir)
        self.library = list(library)
        self._client_running = client_running if client_running is not None else windows_eve_client_running
        self._http = http
        self._esi_names: dict[int, str] = {}
        self._profile = None
        self._characters = []
        self._accounts = []
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.banner = QLabel("")
        self.banner.setObjectName("demoBanner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        layout.addWidget(self.banner)

        steps = QLabel(INSTRUCTIONS)
        steps.setObjectName("notice")
        steps.setWordWrap(True)
        layout.addWidget(steps)

        self.coverage = QPlainTextEdit()
        self.coverage.setReadOnly(True)
        self.coverage.setPlainText(COVERAGE_NOTE)
        self.coverage.setFixedHeight(168)
        layout.addWidget(self.coverage)

        path_row = QHBoxLayout()
        self.path_label = QLabel("No profile folder chosen.")
        self.path_label.setWordWrap(True)
        choose = QPushButton("Choose profile folder")
        choose.clicked.connect(self.choose_profile)
        path_row.addWidget(self.path_label, 1)
        path_row.addWidget(choose)
        layout.addLayout(path_row)

        self.server_label = QLabel("Server: not chosen")
        self.server_label.setObjectName("section")
        layout.addWidget(self.server_label)

        layout.addWidget(QLabel("Source character (copied from)"))
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self._rebuild_targets)
        layout.addWidget(self.source)

        header = QHBoxLayout()
        header.addWidget(QLabel("Target characters (source is excluded)"))
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all(self.targets, True))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_all(self.targets, False))
        header.addWidget(select_all)
        header.addWidget(select_none)
        layout.addLayout(header)
        self.targets = QListWidget()
        self.targets.itemChanged.connect(lambda _item: self.refresh_preview())
        self.targets.setMaximumHeight(140)
        layout.addWidget(self.targets)

        self.account_box = QCheckBox("Also copy account files (core_user) — off unless you pick account ids")
        self.account_box.toggled.connect(self._toggle_accounts)
        layout.addWidget(self.account_box)
        self.account_panel = QVBoxLayout()
        self.account_source = QComboBox()
        self.account_source.currentIndexChanged.connect(self._rebuild_account_targets)
        self.account_targets = QListWidget()
        self.account_targets.itemChanged.connect(lambda _item: self.refresh_preview())
        self.account_targets.setMaximumHeight(90)
        account_buttons = QHBoxLayout()
        account_buttons.addWidget(QLabel("Account source and targets"))
        account_all = QPushButton("Select all accounts")
        account_all.clicked.connect(lambda: self._set_all(self.account_targets, True))
        account_buttons.addWidget(account_all)
        self.account_panel.addLayout(account_buttons)
        self.account_panel.addWidget(self.account_source)
        self.account_panel.addWidget(self.account_targets)
        layout.addLayout(self.account_panel)

        names = QPushButton("Look up public names")
        names.clicked.connect(self.lookup_names)
        layout.addWidget(names)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("The files that would be written appear here. Nothing is written yet.")
        self.preview.setFixedHeight(120)
        layout.addWidget(self.preview)

        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("primary")
        self.copy_button.clicked.connect(self.copy_clicked)
        self.restore_button = QPushButton("Restore selected backup")
        self.restore_button.clicked.connect(self.restore_clicked)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.restore_button)
        layout.addLayout(buttons)
        self.backups = QComboBox()
        layout.addWidget(self.backups)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._toggle_accounts(False)

    def set_fixture_banner(self, text: str) -> None:
        self.banner.setText(text)
        self.banner.show()

    def choose_profile(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose the EVE settings profile folder")
        if selected:
            self.load_profile(Path(selected))

    def load_profile(self, path: Path) -> None:
        try:
            profile = identify_profile(path)
            characters = list_character_files(profile.path)
            accounts = list_account_files(profile.path)
        except (SettingsSyncError, OSError) as exc:
            self._profile = None
            self.status.setText(str(exc))
            return
        self._profile = profile
        self._characters = characters
        self._accounts = accounts
        self.path_label.setText(str(profile.path))
        self.server_label.setText(f"Server: {profile.server_label}. Profile: {profile.profile_name}.")
        self._fill_source(self.source, characters)
        self._fill_source(self.account_source, accounts)
        self._rebuild_targets()
        self._rebuild_account_targets()
        self._reload_backups()
        self.status.setText(
            f"{len(characters)} character file(s), {len(accounts)} account file(s). "
            "Names stay unresolved until you look them up or they are already authorized in Route Desk."
        )

    def _fill_source(self, combo: QComboBox, files) -> None:
        combo.blockSignals(True)
        combo.clear()
        for item in files:
            combo.addItem(self._caption(item.owner_id, item.filename), item.owner_id)
        combo.blockSignals(False)

    def _caption(self, owner_id: int, filename: str) -> str:
        return f"{filename} — {describe_character(owner_id, self.library, self._esi_names)}"

    def _rebuild_targets(self) -> None:
        self._fill_targets(self.targets, self._characters, self._current_id(self.source))
        self.refresh_preview()

    def _rebuild_account_targets(self) -> None:
        self._fill_targets(self.account_targets, self._accounts, self._current_id(self.account_source))
        self.refresh_preview()

    def _fill_targets(self, widget: QListWidget, files, source_id: int | None) -> None:
        checked = self._checked_ids(widget)
        widget.blockSignals(True)
        widget.clear()
        for item in files:
            if source_id is not None and item.owner_id == source_id:
                continue
            row = QListWidgetItem(self._caption(item.owner_id, item.filename))
            row.setData(Qt.ItemDataRole.UserRole, item.owner_id)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked if item.owner_id in checked else Qt.CheckState.Unchecked)
            widget.addItem(row)
        widget.blockSignals(False)

    def _checked_ids(self, widget: QListWidget) -> list[int]:
        ids = []
        for index in range(widget.count()):
            item = widget.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def _current_id(self, combo: QComboBox) -> int | None:
        if combo.currentIndex() < 0:
            return None
        value = combo.currentData()
        return None if value is None else int(value)

    def _set_all(self, widget: QListWidget, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        widget.blockSignals(True)
        for index in range(widget.count()):
            widget.item(index).setCheckState(state)
        widget.blockSignals(False)
        self.refresh_preview()

    def _toggle_accounts(self, enabled: bool) -> None:
        for index in range(self.account_panel.count()):
            item = self.account_panel.itemAt(index)
            widget = item.widget()
            if widget is not None:
                widget.setVisible(enabled)
            nested = item.layout()
            if nested is not None:
                for nested_index in range(nested.count()):
                    nested_widget = nested.itemAt(nested_index).widget()
                    if nested_widget is not None:
                        nested_widget.setVisible(enabled)
        self.refresh_preview()

    def lookup_names(self) -> None:
        if self._profile is None:
            self.status.setText("Choose a profile folder before looking up names.")
            return
        http = self._http or HttpClient()
        ids = [item.owner_id for item in self._characters]
        found = 0
        for character_id in ids:
            name = lookup_public_name(character_id, http)
            if name:
                self._esi_names[character_id] = name
                found += 1
        self._fill_source(self.source, self._characters)
        self._rebuild_targets()
        self.status.setText(
            f"Public ESI returned {found} name(s). Unresolved ids stay as “name not loaded”. No token was sent."
        )

    def current_plans(self) -> list:
        if self._profile is None:
            raise SettingsSyncError("Choose a profile folder.")
        source_id = self._current_id(self.source)
        if source_id is None:
            raise SettingsSyncError("Choose a source character file.")
        plans = plan_copies(self._profile.path, KIND_CHAR, source_id, self._checked_ids(self.targets))
        if self.account_box.isChecked():
            account_source = self._current_id(self.account_source)
            account_targets = self._checked_ids(self.account_targets)
            if account_source is None or not account_targets:
                raise SettingsSyncError(
                    "Account copy is on, but no account source and target are selected. "
                    "Turn it off or pick account ids. Character files were not written."
                )
            plans.extend(plan_copies(self._profile.path, KIND_USER, account_source, account_targets))
        return plans

    def refresh_preview(self) -> None:
        if self._profile is None:
            self.preview.setPlainText("Choose a profile folder. Nothing has been written.")
            return
        try:
            if self.account_box.isChecked() and (
                self._current_id(self.account_source) is None or not self._checked_ids(self.account_targets)
            ):
                source_id = self._current_id(self.source)
                if source_id is None:
                    self.preview.setPlainText("Choose a source character.")
                    return
                character_plans = plan_copies(
                    self._profile.path, KIND_CHAR, source_id, self._checked_ids(self.targets)
                )
                text = (
                    f"Server {self._profile.server_label}. Profile {self._profile.profile_name}.\n"
                    "Character files:\n"
                    f"{describe_plan(character_plans)}\n"
                    "Account files: option on, but no account targets are selected, so no core_user file would be written."
                )
            else:
                plans = self.current_plans()
                character_lines = describe_plan([plan for plan in plans if plan.kind == KIND_CHAR])
                account_plans = [plan for plan in plans if plan.kind == KIND_USER]
                account_lines = (
                    "Account files: not selected. No core_user file would be written."
                    if not account_plans
                    else "Account files:\n" + describe_plan(account_plans)
                )
                text = (
                    f"Server {self._profile.server_label}. Profile {self._profile.profile_name}.\n"
                    "Character files:\n"
                    f"{character_lines}\n{account_lines}"
                )
        except SettingsSyncError as exc:
            text = str(exc)
        self.preview.setPlainText(text + "\nNothing is written until you press Copy and confirm.")

    def copy_clicked(self) -> None:
        try:
            plans = self.current_plans()
        except SettingsSyncError as exc:
            QMessageBox.warning(self, "Cannot copy", str(exc))
            return
        if self._client_running():
            QMessageBox.warning(
                self,
                "EVE is still open",
                "Close every EVE client (exefile.exe) before copying. Nothing was written.",
            )
            return
        summary = describe_plan(plans)
        answer = QMessageBox.question(
            self,
            "Replace these files?",
            "These files will be overwritten or created. A dated backup is stored under Route Desk's data folder.\n\n"
            + summary,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText("Copy cancelled. Nothing was written.")
            return
        self.perform_copy(plans)

    def perform_copy(self, plans=None) -> str:
        try:
            if plans is None:
                plans = self.current_plans()
            manifest = apply_copies(
                self._profile.path,
                plans,
                self.data_dir / BACKUP_DIRNAME,
                clients_running=self._client_running,
            )
        except ClientsStillRunning as exc:
            self.status.setText(str(exc))
            return str(exc)
        except (SettingsSyncError, OSError) as exc:
            self.status.setText(str(exc))
            return str(exc)
        self._reload_backups()
        self.load_profile(self._profile.path)
        message = f"Copied {len(manifest['files'])} file(s). Backup: {manifest['backup_dir']}"
        self.status.setText(message)
        return message

    def _reload_backups(self) -> None:
        self.backups.clear()
        for manifest in list_manifests(self.data_dir / BACKUP_DIRNAME):
            self.backups.addItem(manifest.parent.name, str(manifest))

    def restore_clicked(self) -> None:
        manifest = self.backups.currentData()
        if not manifest or self._profile is None:
            QMessageBox.warning(self, "No backup", "Choose a profile and a backup first. Nothing was written.")
            return
        if self._client_running():
            QMessageBox.warning(self, "EVE is still open", "Close every EVE client before restoring. Nothing was written.")
            return
        answer = QMessageBox.question(
            self,
            "Restore this backup?",
            "The files named in this backup will be put back. Current copies of those files are saved first.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText("Restore cancelled. Nothing was written.")
            return
        try:
            record = restore_manifest(
                self._profile.path,
                Path(manifest),
                self.data_dir / BACKUP_DIRNAME,
                clients_running=self._client_running,
            )
        except (SettingsSyncError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.load_profile(self._profile.path)
        self.status.setText(f"Restored {len(record['files'])} file(s) from {record['restored_from']}.")
