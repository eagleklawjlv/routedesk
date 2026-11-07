"""Route Desk main window."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QHeaderView,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from everoute.account import remember_authorized_character
from everoute.credentials import CredentialError, open_credential_store
from everoute.esi import EsiClient, Outcome, OutcomeKind
from everoute.gui.dialogs import AboutDialog, SetupWizard
from everoute.gui.theme import BAND_COLORS, STATUS_COLORS, app_icon
from everoute.gui.workers import ApplyWorker, LoginWorker, StructureSearchWorker, UniverseWorker
from everoute.http import HttpClient, NetworkError, RequestTimeout, UrlNotAllowed
from everoute.messages import (
    DEMO_BANNER,
    LIVE_BANNER,
    READ_STRUCTURES_SCOPE,
    SCOPE,
    SEARCH_STRUCTURES_SCOPE,
    STORAGE_NOTE,
    STRUCTURE_LIMITATION,
    waypoint_contract,
)
from everoute.models import (
    KIND_STATION,
    KIND_STRUCTURE,
    KIND_SYSTEM,
    LEG_ORIGIN,
    Character,
    Group,
    Stop,
    assign_route,
    duplicate_route,
    move_stop,
    new_id,
    remove_stops,
    reverse_route,
    touch,
)
from everoute.models import Route
from everoute.oauth import AuthError, OAuthClient, TokenManager
from everoute.sample import sample_library
from everoute.stations import StationIndex, load_best_stations
from everoute.structures import StructureCache, StructureDirectory
from everoute.store import Settings, SettingsStore, Store, export_routes, import_routes
from everoute.universe import SystemIndex, load_best_index, security_text
from everoute.waypoints import (
    ApplyJob,
    CancelToken,
    demo_send,
    describe_plan,
    execute_jobs,
    mark_uncertain,
    plan_jobs,
    resume_cancelled,
)

LEG_LABELS = {
    "origin": "Start",
    "gate": "Gate",
    "jump": "Jump",
    "cyno": "Cyno",
}
LABEL_TO_LEG = {label: key for key, label in LEG_LABELS.items()}


def _panel(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(8)
    label = QLabel(title)
    label.setObjectName("section")
    layout.addWidget(label)
    return frame, layout


class MainWindow(QMainWindow):
    def __init__(
        self,
        store: Store,
        settings_store: SettingsStore,
        index: SystemIndex | None = None,
        seed_sample: bool = True,
    ):
        super().__init__()
        self.store = store
        self.settings_store = settings_store
        self.settings = settings_store.load()
        self.index = index if index is not None else load_best_index(store.paths.cache)
        self.station_index = load_best_stations(store.paths.stations)
        self._structure_hits = []
        self.library = store.load()
        self.jobs: list[ApplyJob] = store.load_jobs()
        self._creds = None
        self._tokens: TokenManager | None = None
        self._oauth: OAuthClient | None = None
        self._http: HttpClient | None = None
        self._worker: ApplyWorker | None = None
        self._login_worker: LoginWorker | None = None
        self._universe_worker: UniverseWorker | None = None
        self._cancel = CancelToken()
        self._loading = False
        self._session_demo = False
        self._progress_rows: dict[tuple[str, int | None], int] = {}
        self._checked_ids: set[int] = set()
        self._checks_ready = False
        self.setWindowTitle("Route Desk")
        self._native_taskbar_icon = False
        self.resize(1440, 960)
        self.setMinimumSize(980, 640)
        self._build_ui()
        self._build_menu()
        if seed_sample and not self.library.routes and not self.settings.seeded_sample:
            self.library = sample_library(self.index)
            self.settings.seeded_sample = True
            self._save_library()
            self.settings_store.save(self.settings)
        self.refresh_routes()
        self.refresh_characters()
        self.refresh_groups()
        self._apply_demo_banner()
        if self.jobs:
            self.prepare_progress(self.jobs)
        self.statusBar().showMessage(STORAGE_NOTE)
        self.install_taskbar_icon()

    def install_taskbar_icon(self) -> None:
        """Apply the route-mark icon to this window and, on Windows, the HWND.

        The HWND step runs once the native window exists. It is what the
        taskbar reads when the process is python.exe rather than a frozen
        executable. A shortcut icon is a separate pin and does not replace this.
        """
        self.setWindowIcon(app_icon())
        if self._native_taskbar_icon:
            return
        # Block re-entry: creating the HWND can deliver another show event.
        self._native_taskbar_icon = True
        from everoute.win_taskbar import apply_native_window_icon

        apply_native_window_icon(self)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.install_taskbar_icon()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        root.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 3)

        left, left_layout = _panel("CHARACTERS")
        self.character_filter = QLineEdit()
        self.character_filter.setPlaceholderText("Filter characters")
        self.character_filter.textChanged.connect(self._filter_characters)
        self.character_list = QListWidget()
        self.character_list.setObjectName("characterList")
        self.character_list.itemChanged.connect(self._remember_checks)
        left_layout.addWidget(self.character_filter)
        left_layout.addWidget(self.character_list, 1)
        char_buttons = QHBoxLayout()
        self.add_demo_button = QPushButton("Add demo pilot")
        self.add_demo_button.clicked.connect(self.add_demo_character)
        self.add_sso_button = QPushButton("Authorize character")
        self.add_sso_button.clicked.connect(self.authorize_character)
        self.remove_char_button = QPushButton("Remove checked")
        self.remove_char_button.clicked.connect(self.remove_checked_characters)
        char_buttons.addWidget(self.add_demo_button)
        char_buttons.addWidget(self.add_sso_button)
        char_buttons.addWidget(self.remove_char_button)
        left_layout.addLayout(char_buttons)
        self.assign_button = QPushButton("Assign selected route to checked")
        self.assign_button.clicked.connect(self.assign_selected_to_checked)
        left_layout.addWidget(self.assign_button)
        group_label = QLabel("SETS")
        group_label.setObjectName("section")
        left_layout.addWidget(group_label)
        self.group_list = QListWidget()
        self.group_list.setObjectName("groupList")
        left_layout.addWidget(self.group_list, 1)
        group_buttons = QHBoxLayout()
        check_group = QPushButton("Check this set")
        check_group.clicked.connect(self.check_selected_group)
        new_group = QPushButton("New set from checked")
        new_group.clicked.connect(self.new_group_from_checked)
        delete_group = QPushButton("Delete set")
        delete_group.clicked.connect(self.delete_selected_group)
        group_buttons.addWidget(check_group)
        group_buttons.addWidget(new_group)
        group_buttons.addWidget(delete_group)
        left_layout.addLayout(group_buttons)
        splitter.addWidget(left)

        center, center_layout = _panel("ROUTES")
        library_box = QWidget()
        library_box.setObjectName("panelInner")
        library_layout = QVBoxLayout(library_box)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.setSpacing(6)
        self.route_filter = QLineEdit()
        self.route_filter.setPlaceholderText("Filter saved routes")
        self.route_filter.textChanged.connect(self._filter_routes)
        self.route_list = QListWidget()
        self.route_list.setObjectName("routeList")
        self.route_list.currentItemChanged.connect(self._on_route_selected)
        library_layout.addWidget(self.route_filter)
        library_layout.addWidget(self.route_list, 1)
        route_buttons = QHBoxLayout()
        for text, slot in (
            ("New", self.new_route),
            ("Duplicate", self.duplicate_selected),
            ("Save reversed copy", self.reverse_selected),
            ("Delete", self.delete_selected_route),
            ("Import", self.import_routes_dialog),
            ("Export", self.export_routes_dialog),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            if text == "Save reversed copy":
                button.setToolTip(
                    "Creates a new independent route with the stops reversed. "
                    "The open route is not changed and the copy is not linked to it."
                )
            route_buttons.addWidget(button)
        library_layout.addLayout(route_buttons)

        editor_box = QWidget()
        editor_box.setObjectName("panelInner")
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(6)
        self.route_name = QLineEdit()
        self.route_name.setObjectName("routeName")
        self.route_name.setMaxLength(1_000_000_000)
        self.route_name.setPlaceholderText("Route name")
        self.route_name.textChanged.connect(self._on_name_edited)
        editor_layout.addWidget(self.route_name)
        self.route_notes = QPlainTextEdit()
        self.route_notes.setPlaceholderText("Notes stay on this computer. Leg labels are not sent to EVE.")
        self.route_notes.setFixedHeight(46)
        self.route_notes.textChanged.connect(self._on_notes_edited)
        editor_layout.addWidget(self.route_notes)
        search_row = QHBoxLayout()
        self.system_search = QLineEdit()
        self.system_search.setPlaceholderText("Search systems by name or id")
        self.system_search.returnPressed.connect(self.add_selected_search_hit)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._run_search)
        self.system_search.textChanged.connect(lambda: self._search_timer.start(120))
        add_button = QPushButton("Add system")
        add_button.clicked.connect(self.add_selected_search_hit)
        search_row.addWidget(self.system_search, 1)
        search_row.addWidget(add_button)
        editor_layout.addLayout(search_row)
        self.search_results = QListWidget()
        self.search_results.setObjectName("searchResults")
        self.search_results.setVisible(False)
        self.search_results.setMaximumHeight(120)
        self.search_results.itemDoubleClicked.connect(lambda _item: self.add_selected_search_hit())
        editor_layout.addWidget(self.search_results)
        self.search_results.currentItemChanged.connect(self._on_system_result)
        editor_layout.addWidget(QLabel("Destination in this system"))
        self.destination_combo = QComboBox()
        self.destination_combo.setObjectName("destinationCombo")
        editor_layout.addWidget(self.destination_combo)
        structure_row = QHBoxLayout()
        self.structure_query = QLineEdit()
        self.structure_query.setPlaceholderText("Structure name, 3 or more letters")
        structure_button = QPushButton("Search structures")
        structure_button.clicked.connect(self.search_structures)
        reauthorize = QPushButton("Reauthorize for structure search")
        reauthorize.clicked.connect(self.reauthorize_structures)
        structure_row.addWidget(self.structure_query, 1)
        structure_row.addWidget(structure_button)
        structure_row.addWidget(reauthorize)
        editor_layout.addLayout(structure_row)
        self.structure_note = QLabel(
            "Structures are visible via the checked characters only — docking unverified. "
            "NPC stations in the dropdown need no extra scope."
        )
        self.structure_note.setToolTip(STRUCTURE_LIMITATION)
        self.structure_note.setObjectName("structureNote")
        self.structure_note.setWordWrap(True)
        editor_layout.addWidget(self.structure_note)
        self.structure_results = QListWidget()
        self.structure_results.setObjectName("structureResults")
        self.structure_results.setMaximumHeight(96)
        self.structure_results.itemDoubleClicked.connect(lambda _item: self.add_selected_structure())
        editor_layout.addWidget(self.structure_results)
        id_row = QHBoxLayout()
        self.custom_id = QLineEdit()
        self.custom_id.setPlaceholderText("Or a station / structure id")
        add_id = QPushButton("Add id")
        add_id.clicked.connect(self.add_custom_id)
        id_row.addWidget(self.custom_id, 1)
        id_row.addWidget(add_id)
        editor_layout.addLayout(id_row)
        edit_row = QHBoxLayout()
        up = QPushButton("Up")
        down = QPushButton("Down")
        remove = QPushButton("Remove")
        up.clicked.connect(lambda: self.move_selected_stop(-1))
        down.clicked.connect(lambda: self.move_selected_stop(1))
        remove.clicked.connect(self.remove_selected_stops)
        self.leg_combo = QComboBox()
        self.leg_combo.setObjectName("legCombo")
        self.leg_combo.setToolTip(
            "Planning label for how you intend to arrive at the selected stop. "
            "Not sent to ESI. Not a jump-range or fuel check."
        )
        self.leg_combo.currentTextChanged.connect(self._on_leg_combo)
        edit_row.addWidget(up)
        edit_row.addWidget(down)
        edit_row.addWidget(remove)
        edit_row.addStretch(1)
        edit_row.addWidget(QLabel("Travel to selected stop"))
        edit_row.addWidget(self.leg_combo)
        editor_layout.addLayout(edit_row)
        self.stop_table = QTableWidget(0, 6)
        self.stop_table.setObjectName("stopTable")
        self.stop_table.setHorizontalHeaderLabels(["#", "System", "Destination", "Sec", "Region", "Travel to here"])
        self.stop_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stop_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.stop_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stop_table.verticalHeader().setVisible(False)
        self.stop_table.setAlternatingRowColors(True)
        self.stop_table.itemSelectionChanged.connect(self._sync_leg_combo)
        stop_header = self.stop_table.horizontalHeader()
        stop_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        stop_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        stop_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        stop_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        stop_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        stop_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        editor_layout.addWidget(self.stop_table, 1)
        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.setChildrenCollapsible(False)
        vertical.addWidget(library_box)
        vertical.addWidget(editor_box)
        vertical.setSizes([120, 640])
        center_layout.addWidget(vertical)
        splitter.addWidget(center)

        right, right_layout = _panel("PREVIEW")
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("preview")
        self.preview.setReadOnly(True)
        right_layout.addWidget(self.preview, 1)
        self.contract_note = QLabel()
        self.contract_note.setObjectName("notice")
        self.contract_note.setWordWrap(True)
        right_layout.addWidget(self.contract_note)
        splitter.addWidget(right)
        splitter.setSizes([300, 700, 320])

        bottom, bottom_layout = _panel("APPLY")
        mode_row = QHBoxLayout()
        self.replace_radio = QRadioButton("Replace existing autopilot waypoints")
        self.append_radio = QRadioButton("Append after waypoints already in the client")
        self.replace_radio.setChecked(True)
        mode_row.addWidget(self.replace_radio)
        mode_row.addWidget(self.append_radio)
        mode_row.addStretch(1)
        bottom_layout.addLayout(mode_row)
        target_row = QHBoxLayout()
        self.target_selected = QRadioButton("Checked characters all get the selected route")
        self.target_assigned = QRadioButton("Each checked character gets their assigned route")
        self.target_selected.setChecked(True)
        target_row.addWidget(self.target_selected)
        target_row.addWidget(self.target_assigned)
        target_row.addStretch(1)
        bottom_layout.addLayout(target_row)
        action_row = QHBoxLayout()
        self.apply_button = QPushButton("Set waypoints")
        self.apply_button.setObjectName("applyButton")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.apply_clicked)
        self.cancel_button = QPushButton("Cancel remaining")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_apply)
        self.retry_button = QPushButton("Retry from the failed stop")
        self.retry_button.clicked.connect(self.retry_apply)
        self.resume_button = QPushButton("Resume cancelled")
        self.resume_button.clicked.connect(self.resume_apply)
        self.uncertain_no = QPushButton("Uncertain: send again")
        self.uncertain_yes = QPushButton("Uncertain: mark applied")
        self.uncertain_no.clicked.connect(lambda: self.resolve_uncertain(False))
        self.uncertain_yes.clicked.connect(lambda: self.resolve_uncertain(True))
        action_row.addWidget(self.apply_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.retry_button)
        action_row.addWidget(self.resume_button)
        action_row.addWidget(self.uncertain_no)
        action_row.addWidget(self.uncertain_yes)
        action_row.addStretch(1)
        bottom_layout.addLayout(action_row)
        self.storage_note = QLabel(STORAGE_NOTE)
        self.storage_note.setObjectName("storageNote")
        self.storage_note.setWordWrap(True)
        bottom_layout.addWidget(self.storage_note)
        self.progress = QTableWidget(0, 5)
        self.progress.setObjectName("progressTable")
        self.progress.setHorizontalHeaderLabels(["Character", "Route", "Stop", "Result", "Detail"])
        self.progress.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.progress.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.progress.verticalHeader().setVisible(False)
        self.progress.setMinimumHeight(120)
        progress_header = self.progress.horizontalHeader()
        progress_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        bottom_layout.addWidget(self.progress, 1)
        root.addWidget(bottom, 2)
        self.setStatusBar(QStatusBar())
        # The primary id was overwritten by setObjectName("primary") for styling.
        self.apply_button.setObjectName("primary")

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction("New route", self.new_route, QKeySequence("Ctrl+N"))
        file_menu.addAction("Import routes", self.import_routes_dialog)
        file_menu.addAction("Export routes", self.export_routes_dialog)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close)
        character_menu = self.menuBar().addMenu("Characters")
        character_menu.addAction("Add demo pilot", self.add_demo_character)
        character_menu.addAction("Authorize character (waypoint only)", self.authorize_character)
        character_menu.addAction("Remove checked", self.remove_checked_characters)
        mode_menu = self.menuBar().addMenu("Mode")
        self.demo_action = QAction("Demo / test mode", self)
        self.demo_action.setCheckable(True)
        self.demo_action.setChecked(self.settings.demo_mode)
        self.demo_action.toggled.connect(self.set_demo_mode)
        mode_menu.addAction(self.demo_action)
        tools = self.menuBar().addMenu("Tools")
        tools.addAction("SSO setup", self.open_setup)
        tools.addAction("Copy EVE character settings", self.open_settings_sync)
        tools.addAction("Refresh public universe cache", self.refresh_universe)
        tools.addAction("About", self.open_about)

    def effective_demo(self) -> bool:
        return bool(self._session_demo or self.settings.demo_mode)

    def use_session_demo(self) -> None:
        """Show demo mode for this window only. The saved setting is not changed."""
        self._session_demo = True
        self._apply_demo_banner()

    def _apply_demo_banner(self) -> None:
        demo = self.effective_demo()
        self.banner.setObjectName("demoBanner" if demo else "liveBanner")
        self.banner.setText(DEMO_BANNER if demo else LIVE_BANNER)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.apply_button.setText("Set waypoints (demo, simulated)" if demo else "Set waypoints")
        if self.demo_action.isChecked() != demo:
            self.demo_action.blockSignals(True)
            self.demo_action.setChecked(demo)
            self.demo_action.blockSignals(False)

    def set_demo_mode(self, enabled: bool) -> None:
        if enabled is False and not self.settings.client_id:
            self.open_setup()
            if not self.settings.client_id:
                self.demo_action.blockSignals(True)
                self.demo_action.setChecked(True)
                self.demo_action.blockSignals(False)
                self.statusBar().showMessage("Live mode needs your SSO client ID. Demo mode stays on.")
                return
        self._session_demo = False
        self.settings.demo_mode = bool(enabled)
        self.settings_store.save(self.settings)
        self._reset_auth()
        self._apply_demo_banner()

    def reload_account_from_disk(self) -> None:
        """Pick up a CLI setup or authorize without discarding route edits."""
        fresh_settings = self.settings_store.load()
        account_changed = (
            fresh_settings.demo_mode != self.settings.demo_mode
            or fresh_settings.client_id != self.settings.client_id
            or fresh_settings.callback_port != self.settings.callback_port
            or fresh_settings.callback_host != self.settings.callback_host
            or fresh_settings.allow_file_token_store != self.settings.allow_file_token_store
        )
        if account_changed:
            self.settings.demo_mode = fresh_settings.demo_mode
            self.settings.client_id = fresh_settings.client_id
            self.settings.callback_host = fresh_settings.callback_host
            self.settings.callback_port = fresh_settings.callback_port
            self.settings.allow_file_token_store = fresh_settings.allow_file_token_store
            self._reset_auth()
            self._apply_demo_banner()
        fresh = self.store.load()

        def signature(characters):
            return [
                (item.character_id, item.character_name, item.demo, item.assigned_route_id, tuple(item.scopes))
                for item in characters
            ]

        if signature(fresh.characters) != signature(self.library.characters):
            self.library.characters = fresh.characters
            self.refresh_characters()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.reload_account_from_disk()
        super().changeEvent(event)

    def _save_library(self) -> None:
        try:
            self.store.save(self.library)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save", f"The library was not written.\n{exc}")

    def refresh_routes(self) -> None:
        current = self.current_route_id()
        self.route_list.blockSignals(True)
        self.route_list.clear()
        chosen = None
        fallback = None
        for route in self.library.routes:
            item = QListWidgetItem(f"{route.name}    {len(route.stops)} stops")
            item.setData(Qt.ItemDataRole.UserRole, route.id)
            self.route_list.addItem(item)
            if route.id == current:
                chosen = item
            elif route.id == self.settings.last_route_id:
                fallback = item
        self.route_list.setCurrentItem(chosen or fallback)
        if self.route_list.currentRow() < 0 and self.route_list.count():
            self.route_list.setCurrentRow(0)
        self.route_list.blockSignals(False)
        self._filter_routes(self.route_filter.text())
        self.load_selected_route()

    def refresh_characters(self) -> None:
        self.character_list.blockSignals(True)
        self.character_list.clear()
        if not self._checks_ready:
            self._checked_ids = {character.character_id for character in self.library.characters}
            self._checks_ready = True
        for character in self.library.characters:
            route = self.library.route(character.assigned_route_id)
            assigned = route.name if route else "no route assigned"
            kind = "demo" if character.demo else "authorized"
            item = QListWidgetItem(f"{character.character_name}   ·   {assigned}   ·   {kind}")
            item.setData(Qt.ItemDataRole.UserRole, character.character_id)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            checked = character.character_id in self._checked_ids
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.character_list.addItem(item)
        self.character_list.blockSignals(False)
        self._remember_checks()
        self._filter_characters(self.character_filter.text())

    def refresh_groups(self) -> None:
        current = self.group_list.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.group_list.clear()
        for group in self.library.groups:
            item = QListWidgetItem(f"{group.name}    {len(group.character_ids)}")
            item.setData(Qt.ItemDataRole.UserRole, group.id)
            self.group_list.addItem(item)
            if group.id == current_id:
                self.group_list.setCurrentItem(item)

    def current_route_id(self) -> str | None:
        item = self.route_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def current_route(self) -> Route | None:
        return self.library.route(self.current_route_id())

    def select_route_by_name(self, name: str) -> None:
        for row in range(self.route_list.count()):
            item = self.route_list.item(row)
            route = self.library.route(item.data(Qt.ItemDataRole.UserRole))
            if route and route.name == name:
                self.route_list.setCurrentItem(item)
                return
        raise LookupError(name)

    def load_selected_route(self) -> None:
        route = self.current_route()
        self._loading = True
        try:
            self.route_name.setText(route.name if route else "")
            self.route_notes.setPlainText(route.notes if route else "")
            self._fill_stop_table(route)
            self._refresh_preview()
        finally:
            self._loading = False
        if route is not None:
            self.settings.last_route_id = route.id
            self.settings_store.save(self.settings)
            if route.stops:
                self.fill_destination_combo(route.stops[0].system_id)

    def _on_route_selected(self, *_args) -> None:
        self.load_selected_route()

    def _fill_stop_table(self, route: Route | None) -> None:
        stops = route.stops if route else []
        self.stop_table.setRowCount(len(stops))
        for index, stop in enumerate(stops):
            system = self.index.get(stop.system_id)
            security = stop.security if stop.security is not None else (system.security if system else None)
            region = stop.region_name or (system.region_name if system else "")
            band = system.band if system else ("unknown" if security is None else _band(security))
            values = [
                str(index + 1),
                stop.system_name,
                stop.destination_name or stop.system_name,
                security_text(system, stop.security),
                region or "—",
                LEG_LABELS.get(stop.leg_type, stop.leg_type),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if column == 3:
                    item.setForeground(QColor(BAND_COLORS.get(band, BAND_COLORS["unknown"])))
                    item.setToolTip(
                        "Security from the universe cache when it is known. "
                        "High is 0.45 or more, low is above 0, null is 0 or below. "
                        "Not a jump-range check."
                    )
                if column == 2:
                    tip = stop.visibility or f"{stop.destination_kind} id {stop.destination_id}"
                    item.setToolTip(tip)
                self.stop_table.setItem(index, column, item)
        if stops:
            self.stop_table.selectRow(0)
        self._sync_leg_combo()

    def _refresh_preview(self) -> None:
        route = self.current_route()
        if route is None:
            self.preview.setPlainText("Create a route. There is no limit on how many you can save.")
            self.contract_note.setText(waypoint_contract(0))
            return
        lines = [route.name, ""]
        for index, stop in enumerate(route.stops):
            if index:
                label = LEG_LABELS.get(stop.leg_type, stop.leg_type)
                lines.append(f"    {label.lower()}  (planning label only)")
            system = self.index.get(stop.system_id)
            security = security_text(system, stop.security)
            region = stop.region_name or (system.region_name if system else "")
            place = stop.destination_name or stop.system_name
            extra = ""
            if int(stop.destination_id) != int(stop.system_id):
                extra = f"   destination {stop.destination_id}"
            if stop.visibility:
                extra += f"   {stop.visibility}"
            lines.append(f"{index + 1}. {stop.system_name} — {place}   {security}   {region}{extra}")
        if not route.stops:
            lines.append("No stops yet. Search a system and add it. Midpoints are not capped.")
        lines.append("")
        lines.append(route.notes)
        self.preview.setPlainText("\n".join(lines))
        self.contract_note.setText(waypoint_contract(len(route.stops)))

    def _on_name_edited(self, text: str) -> None:
        if self._loading:
            return
        route = self.current_route()
        if route is None:
            return
        route.name = text.strip() or "Untitled route"
        touch(route)
        self._save_library()
        item = self.route_list.currentItem()
        if item is not None:
            item.setText(f"{route.name}    {len(route.stops)} stops")
        self._refresh_preview()

    def _on_notes_edited(self) -> None:
        if self._loading:
            return
        route = self.current_route()
        if route is None:
            return
        route.notes = self.route_notes.toPlainText()
        touch(route)
        self._save_library()

    def new_route(self) -> None:
        route = Route(id=new_id(), name="Untitled route", stops=[])
        touch(route)
        self.library.routes.append(route)
        self._save_library()
        self.refresh_routes()
        for row in range(self.route_list.count()):
            item = self.route_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == route.id:
                self.route_list.setCurrentItem(item)
                break
        self.route_name.setFocus()

    def duplicate_selected(self) -> None:
        route = self.current_route()
        if route is None:
            return
        copy = duplicate_route(route)
        self.library.routes.append(copy)
        self._save_library()
        self.refresh_routes()
        self.statusBar().showMessage("Created an independent copy. The original route was not changed.")

    def reverse_selected(self) -> None:
        route = self.current_route()
        if route is None:
            return
        if not route.stops:
            self.statusBar().showMessage("Add stops before reversing.")
            return
        copy = reverse_route(route)
        self.library.routes.append(copy)
        self._save_library()
        self.refresh_routes()
        for row in range(self.route_list.count()):
            item = self.route_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == copy.id:
                self.route_list.setCurrentItem(item)
                break
        self.statusBar().showMessage(
            "Created an independent reversed copy. The original route was not changed."
        )

    def delete_selected_route(self, confirm: bool = True) -> None:
        route = self.current_route()
        if route is None:
            return
        if confirm:
            answer = QMessageBox.question(
                self,
                "Delete route",
                f"Delete “{route.name}”? Other routes are not affected.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.library.routes = [item for item in self.library.routes if item.id != route.id]
        for character in self.library.characters:
            if character.assigned_route_id == route.id:
                character.assigned_route_id = None
        self._save_library()
        self.refresh_routes()
        self.refresh_characters()

    def _run_search(self) -> None:
        self.search_results.clear()
        query = self.system_search.text()
        for system in self.index.search(query):
            security = security_text(system)
            region = system.region_name or "—"
            item = QListWidgetItem(f"{system.name}    {security}    {region}")
            item.setData(Qt.ItemDataRole.UserRole, system.system_id)
            self.search_results.addItem(item)
        self.search_results.setVisible(self.search_results.count() > 0)

    def fill_destination_combo(self, system_id: int) -> None:
        system = self.index.get(int(system_id))
        name = system.name if system else str(system_id)
        self.destination_combo.blockSignals(True)
        self.destination_combo.clear()
        self.destination_combo.addItem(
            f"System only — {name}",
            (KIND_SYSTEM, int(system_id), int(system_id), name, "", []),
        )
        for station in self.station_index.in_system(system_id):
            self.destination_combo.addItem(
                station.name,
                (KIND_STATION, int(station.station_id), int(station.system_id), station.name, "", []),
            )
        self.destination_combo.blockSignals(False)

    def _on_system_result(self, current, _previous=None) -> None:
        if current is None:
            return
        self.fill_destination_combo(int(current.data(Qt.ItemDataRole.UserRole)))

    def add_selected_search_hit(self) -> None:
        route = self.current_route()
        if route is None:
            self.statusBar().showMessage("Create a route first.")
            return
        item = self.search_results.currentItem()
        if item is None and self.search_results.count():
            item = self.search_results.item(0)
        if item is None:
            exact = self.index.exact(self.system_search.text().strip())
            if exact is None and self.destination_combo.count() == 0:
                self.statusBar().showMessage("No matching system.")
                return
            system_id = exact.system_id if exact is not None else None
        else:
            system_id = int(item.data(Qt.ItemDataRole.UserRole))
        choice = self.destination_combo.currentData()
        if choice is not None and (system_id is None or int(choice[2]) == int(system_id)):
            kind, destination_id, system_id, destination_name, visibility, seen_by = choice
            system = self.index.get(int(system_id))
        else:
            if system_id is None:
                self.statusBar().showMessage("No matching system.")
                return
            system = self.index.get(system_id)
            if system is None:
                return
            kind, destination_id, destination_name, visibility, seen_by = (
                KIND_SYSTEM,
                system.system_id,
                system.name,
                "",
                [],
            )
        if system is None:
            system_name = str(system_id)
            security = None
            region = ""
        else:
            system_name = system.name
            security = system.security
            region = system.region_name
        self._append_stop(
            Stop(
                system_id=int(system_id),
                system_name=system_name,
                leg_type=LEG_ORIGIN if not route.stops else "gate",
                security=security,
                region_name=region,
                destination_id=int(destination_id),
                destination_name=destination_name,
                destination_kind=kind,
                visibility=visibility,
                seen_by=list(seen_by),
            )
        )

    def add_custom_id(self) -> None:
        route = self.current_route()
        if route is None:
            return
        text = self.custom_id.text().strip()
        if not text or text in ("-",) or not text.lstrip("-").isdigit():
            self.statusBar().showMessage("Enter an integer destination id.")
            return
        number = int(text)
        station = self.station_index.get(number)
        if station is not None:
            system = self.index.get(station.system_id)
            stop = Stop(
                system_id=station.system_id,
                system_name=system.name if system else str(station.system_id),
                leg_type="gate",
                security=None if system is None else system.security,
                region_name="" if system is None else system.region_name,
                destination_id=station.station_id,
                destination_name=station.name,
                destination_kind=KIND_STATION,
            )
        else:
            system = self.index.get(number)
            if system is not None:
                stop = Stop(
                    system_id=system.system_id,
                    system_name=system.name,
                    leg_type="gate",
                    security=system.security,
                    region_name=system.region_name,
                )
            else:
                self._resolve_manual_structure(number)
                return
        if not route.stops:
            stop.leg_type = LEG_ORIGIN
        self._append_stop(stop)
        self.custom_id.clear()

    def discovery_characters(self):
        checked = set(self.checked_character_ids())
        return [character for character in self.library.characters if character.character_id in checked and not character.demo]

    def search_structures(self) -> None:
        characters = self.discovery_characters()
        if not characters:
            self.statusBar().showMessage(
                "Check an authorized character. Demo pilots are not used for structure search."
            )
            return
        self._start_structure_lookup(characters, self.structure_query.text(), None)

    def _resolve_manual_structure(self, structure_id: int) -> None:
        characters = self.discovery_characters()
        if not characters:
            self.statusBar().showMessage(
                "Check a character who was authorized for structure search, or add the station from the system list."
            )
            return
        self._start_structure_lookup(characters, "", structure_id)

    def _start_structure_lookup(self, characters, query: str, structure_id: int | None) -> None:
        if self.effective_demo():
            self.statusBar().showMessage("Demo mode does not contact ESI. Turn it off to search structures.")
            return
        try:
            manager = self._ensure_tokens()
        except CredentialError as exc:
            QMessageBox.warning(self, "Credential store", str(exc))
            return
        directory = StructureDirectory(
            self._http_client(),
            StructureCache(self.store.paths.structure_cache),
        )
        self._structure_worker = StructureSearchWorker(
            directory,
            characters,
            query,
            manager.access_token,
            structure_id,
            self,
        )
        self._structure_worker.succeeded.connect(self._show_structure_discovery)
        self._structure_worker.failed.connect(
            lambda message: self.statusBar().showMessage(message)
        )
        self._structure_worker.start()
        self.statusBar().showMessage("Searching structures for the checked characters…")

    def _show_structure_discovery(self, discovery) -> None:
        self.structure_results.clear()
        self._structure_hits = list(discovery.hits)
        if discovery.query_error:
            self.statusBar().showMessage(discovery.query_error)
        for problem in discovery.problems:
            item = QListWidgetItem(f"{problem.character_name}: {problem.message}")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.structure_results.addItem(item)
        for index, hit in enumerate(self._structure_hits):
            system = self.index.get(hit.system_id)
            system_name = system.name if system else str(hit.system_id)
            item = QListWidgetItem(f"{hit.name}   {system_name}   {hit.label()}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.structure_results.addItem(item)
        if not self._structure_hits and not discovery.problems and not discovery.query_error:
            self.statusBar().showMessage("No structure was visible to the checked characters.")

    def add_selected_structure(self) -> None:
        item = self.structure_results.currentItem()
        if item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return
        hit = self._structure_hits[int(index)]
        system = self.index.get(hit.system_id)
        route = self.current_route()
        if route is None:
            return
        self._append_stop(
            Stop(
                system_id=hit.system_id,
                system_name=system.name if system else str(hit.system_id),
                leg_type=LEG_ORIGIN if not route.stops else "gate",
                security=None if system is None else system.security,
                region_name="" if system is None else system.region_name,
                destination_id=hit.structure_id,
                destination_name=hit.name,
                destination_kind=KIND_STRUCTURE,
                seen_by=[character_id for character_id, _name in hit.seen_by],
                visibility=hit.label(),
            )
        )

    def reauthorize_structures(self) -> None:
        if self.effective_demo():
            QMessageBox.information(
                self,
                "Demo mode",
                "Turn demo mode off before authorizing. NPC station lists already work without this.",
            )
            return
        if not self.settings.client_id:
            self.open_setup()
            if not self.settings.client_id:
                return
        try:
            self._ensure_tokens()
        except CredentialError as exc:
            QMessageBox.warning(self, "Credential store", str(exc))
            return
        self._begin_login(
            [SCOPE, SEARCH_STRUCTURES_SCOPE, READ_STRUCTURES_SCOPE],
            "Browser login will ask for waypoint and structure scopes. Add the structure scopes on the existing application first.",
        )

    def _append_stop(self, stop: Stop) -> None:
        route = self.current_route()
        if route is None:
            return
        route.stops.append(stop)
        if route.stops:
            route.stops[0].leg_type = LEG_ORIGIN
        touch(route)
        self._save_library()
        self._fill_stop_table(route)
        self._refresh_preview()
        item = self.route_list.currentItem()
        if item is not None:
            item.setText(f"{route.name}    {len(route.stops)} stops")
        self.stop_table.scrollToBottom()

    def selected_stop_rows(self) -> list[int]:
        return sorted({index.row() for index in self.stop_table.selectionModel().selectedRows()})

    def remove_selected_stops(self) -> None:
        route = self.current_route()
        if route is None:
            return
        rows = self.selected_stop_rows()
        if not rows:
            return
        remove_stops(route.stops, rows)
        touch(route)
        self._save_library()
        self._fill_stop_table(route)
        self._refresh_preview()
        item = self.route_list.currentItem()
        if item is not None:
            item.setText(f"{route.name}    {len(route.stops)} stops")

    def move_selected_stop(self, delta: int) -> None:
        route = self.current_route()
        rows = self.selected_stop_rows()
        if route is None or len(rows) != 1:
            return
        move_stop(route.stops, rows[0], delta)
        touch(route)
        self._save_library()
        self._fill_stop_table(route)
        next_row = rows[0] + delta
        if 0 <= next_row < self.stop_table.rowCount():
            self.stop_table.selectRow(next_row)
        self._refresh_preview()

    def _sync_leg_combo(self) -> None:
        route = self.current_route()
        row = self.stop_table.currentRow()
        self.leg_combo.blockSignals(True)
        self.leg_combo.clear()
        if route is None or row < 0 or row >= len(route.stops):
            self.leg_combo.setEnabled(False)
        elif row == 0:
            self.leg_combo.addItem("Start")
            self.leg_combo.setEnabled(False)
        else:
            current = route.stops[row].leg_type
            labels = ["Gate", "Jump", "Cyno"]
            extra = LEG_LABELS.get(current, current)
            if extra not in labels:
                labels.append(extra)
            self.leg_combo.addItems(labels)
            self.leg_combo.setCurrentText(extra if extra in labels else "Gate")
            self.leg_combo.setEnabled(True)
        self.leg_combo.blockSignals(False)

    def _on_leg_combo(self, label: str) -> None:
        if self._loading or not label:
            return
        route = self.current_route()
        row = self.stop_table.currentRow()
        if route is None or row <= 0 or row >= len(route.stops):
            return
        route.stops[row].leg_type = LABEL_TO_LEG.get(label, label)
        touch(route)
        self._save_library()
        cell = self.stop_table.item(row, 5)
        if cell is not None:
            cell.setText(LEG_LABELS.get(route.stops[row].leg_type, route.stops[row].leg_type))
        self._refresh_preview()

    def set_leg(self, index: int, leg: str) -> None:
        self.stop_table.selectRow(index)
        if index == 0:
            return
        label = LEG_LABELS.get(leg, leg)
        self.leg_combo.setCurrentText(label)

    def leg_at(self, index: int) -> str:
        route = self.current_route()
        if route is None:
            return ""
        return route.stops[index].leg_type

    def stop_names(self) -> list[str]:
        route = self.current_route()
        if route is None:
            return []
        return [stop.system_name for stop in route.stops]

    def route_count(self) -> int:
        return len(self.library.routes)

    def visible_route_count(self) -> int:
        return sum(1 for row in range(self.route_list.count()) if not self.route_list.item(row).isHidden())

    def _filter_routes(self, text: str) -> None:
        query = text.casefold().strip()
        for row in range(self.route_list.count()):
            item = self.route_list.item(row)
            item.setHidden(bool(query) and query not in item.text().casefold())

    def _filter_characters(self, text: str) -> None:
        query = text.casefold().strip()
        for row in range(self.character_list.count()):
            item = self.character_list.item(row)
            item.setHidden(bool(query) and query not in item.text().casefold())

    def _remember_checks(self, *_args) -> None:
        if self.character_list.signalsBlocked():
            return
        self._checked_ids = set(self.checked_character_ids())

    def checked_character_ids(self) -> list[int]:
        ids = []
        for row in range(self.character_list.count()):
            item = self.character_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def check_all_characters(self) -> None:
        for row in range(self.character_list.count()):
            self.character_list.item(row).setCheckState(Qt.CheckState.Checked)

    def check_only(self, character_ids: list[int]) -> None:
        wanted = set(character_ids)
        self._checked_ids = set(wanted)
        for row in range(self.character_list.count()):
            item = self.character_list.item(row)
            state = Qt.CheckState.Checked if int(item.data(Qt.ItemDataRole.UserRole)) in wanted else Qt.CheckState.Unchecked
            item.setCheckState(state)

    def set_target(self, target: str) -> None:
        self.target_selected.setChecked(target == "selected")
        self.target_assigned.setChecked(target == "assigned")

    def set_waypoint_mode(self, mode: str) -> None:
        self.replace_radio.setChecked(mode == "replace")
        self.append_radio.setChecked(mode == "append")

    def assign_selected_to_checked(self) -> None:
        route = self.current_route()
        ids = self.checked_character_ids()
        if route is None or not ids:
            self.statusBar().showMessage("Select a route and check the characters who should use it.")
            return
        assign_route(self.library.characters, ids, route.id)
        self._save_library()
        self.refresh_characters()
        self.statusBar().showMessage(f"Assigned “{route.name}” to {len(ids)} character(s).")

    def check_selected_group(self) -> None:
        item = self.group_list.currentItem()
        if item is None:
            return
        group = next((group for group in self.library.groups if group.id == item.data(Qt.ItemDataRole.UserRole)), None)
        if group is None:
            return
        self.check_only(group.character_ids)

    def new_group_from_checked(self) -> None:
        ids = self.checked_character_ids()
        if not ids:
            self.statusBar().showMessage("Check the characters that belong in the set.")
            return
        name, ok = QInputDialog.getText(self, "Character set", "Name")
        if not ok or not name.strip():
            return
        self.library.groups.append(Group(id=new_id(), name=name.strip(), character_ids=list(ids)))
        self._save_library()
        self.refresh_groups()

    def delete_selected_group(self) -> None:
        item = self.group_list.currentItem()
        if item is None:
            return
        group_id = item.data(Qt.ItemDataRole.UserRole)
        self.library.groups = [group for group in self.library.groups if group.id != group_id]
        self._save_library()
        self.refresh_groups()

    def add_demo_character(self) -> None:
        name, ok = QInputDialog.getText(self, "Demo pilot", "Name")
        if not ok or not name.strip():
            return
        used = {character.character_id for character in self.library.characters}
        character_id = 2112000001
        while character_id in used:
            character_id += 1
        self.library.characters.append(
            Character(character_id=character_id, character_name=name.strip(), demo=True)
        )
        self._save_library()
        self.refresh_characters()

    def remove_checked_characters(self) -> None:
        ids = self.checked_character_ids()
        if not ids:
            return
        names = [self.library.character(character_id).character_name for character_id in ids if self.library.character(character_id)]
        answer = QMessageBox.question(self, "Remove characters", "Remove " + ", ".join(names) + "?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        for character_id in ids:
            character = self.library.character(character_id)
            if character is not None:
                self._drop_token(character)
        self.library.characters = [character for character in self.library.characters if character.character_id not in ids]
        for group in self.library.groups:
            group.character_ids = [character_id for character_id in group.character_ids if character_id not in ids]
        self._checked_ids.difference_update(ids)
        self._save_library()
        self.refresh_characters()
        self.refresh_groups()

    def _drop_token(self, character: Character) -> None:
        if character.demo:
            return
        if self.effective_demo():
            self.statusBar().showMessage(
                "Demo mode did not contact CCP. Turn demo off and remove the character again to revoke, "
                "or revoke the application on the EVE website."
            )
            return
        try:
            manager = self._ensure_tokens()
            refresh = manager.credentials.get_refresh_token(character.character_id)
            if refresh:
                self._oauth_client().revoke(refresh)
            manager.forget(character.character_id)
            manager.credentials.delete_refresh_token(character.character_id)
            self.statusBar().showMessage("Refresh token revoked and removed from this computer.")
        except Exception as exc:
            self.statusBar().showMessage(f"Could not revoke the token ({exc}). Remove it on the EVE website if it still exists.")

    def authorize_character(self) -> None:
        if self.effective_demo():
            QMessageBox.information(
                self,
                "Demo mode",
                "Demo mode does not contact EVE. Turn it off in Mode after SSO setup, then authorize a character.",
            )
            return
        if not self.settings.client_id:
            self.open_setup()
            if not self.settings.client_id:
                return
        try:
            self._ensure_tokens()
        except CredentialError as exc:
            QMessageBox.warning(self, "Credential store", str(exc))
            return
        self._begin_login(None, "Waiting for the EVE login redirect on this computer.")

    def _begin_login(self, scopes: list[str] | None, waiting: str) -> None:
        requested = list(scopes) if scopes else [SCOPE]
        if not self._confirm_login(requested, structure_search=scopes is not None):
            self.statusBar().showMessage("Login cancelled before the browser opened. No token was changed.")
            return
        self._cancel_login()
        self._login_worker = LoginWorker(
            self._oauth_client(),
            int(self.settings.callback_port),
            self,
            scopes=scopes,
        )
        self._login_worker.succeeded.connect(self._on_login)
        self._login_worker.failed.connect(self._on_login_failed)
        self._login_worker.cancelled.connect(self._on_login_cancelled)
        self.statusBar().showMessage(waiting + " Requested: " + " ".join(requested))
        self._login_worker.start()

    def _confirm_login(self, requested: list[str], *, structure_search: bool) -> bool:
        lines = "\n".join(requested)
        if structure_search:
            title = "Reauthorize for structure search"
            body = (
                "This login requests these scopes:\n\n"
                f"{lines}\n\n"
                "On the EVE page, Required scopes should list all of them, not only the waypoint scope. "
                "publicData is added by EVE and is not one of these requests.\n\n"
                "Select the character on that page. Checkboxes in this window do not choose the character."
            )
        else:
            title = "Authorize character"
            body = (
                "This login requests only this scope:\n\n"
                f"{lines}\n\n"
                "It does not ask for structure search. Use Reauthorize for structure search for that.\n\n"
                "Select the character on the EVE page. Checkboxes in this window do not choose the character."
            )
        answer = QMessageBox.question(self, title, body)
        return answer == QMessageBox.StandardButton.Yes

    def _cancel_login(self) -> None:
        worker = self._login_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(3000)

    def _on_login_failed(self, message: str) -> None:
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "EVE login", message)

    def _on_login_cancelled(self, message: str) -> None:
        self.statusBar().showMessage(message or "Login cancelled. No token was stored.")

    def _on_login(self, payload: dict) -> None:
        tokens = payload["tokens"]
        try:
            manager = self._ensure_tokens()
            character = remember_authorized_character(
                self.library, manager.credentials, payload["claims"], tokens
            )
        except (CredentialError, AuthError) as exc:
            QMessageBox.warning(self, "EVE login", str(exc))
            return
        access = tokens.get("access_token")
        if access:
            manager._access[character.character_id] = (
                access,
                time.time() + float(tokens.get("expires_in") or 1200),
            )
        self._save_library()
        self.refresh_characters()
        scopes = ", ".join(character.scopes) or "the waypoint scope"
        self.statusBar().showMessage(f"Authorized {character.character_name}. Only {scopes} was accepted.")

    def open_setup(self) -> None:
        from PySide6.QtWidgets import QDialog

        from everoute.credentials import keyring_works

        ok, _detail = keyring_works()
        draft = Settings.from_dict(self.settings.to_dict())
        wizard = SetupWizard(draft, ok, self)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings = draft
        self.settings_store.save(self.settings)
        self._reset_auth()
        self._apply_demo_banner()
        self.statusBar().showMessage("Saved SSO settings. No client secret was stored.")

    def open_settings_sync(self) -> None:
        from everoute.gui.settings_sync import SettingsSyncDialog

        dialog = SettingsSyncDialog(self.store.paths.root, self.library.characters, parent=self)
        dialog.setWindowIcon(self.windowIcon())
        dialog.exec()

    def open_about(self) -> None:
        AboutDialog(self).exec()

    def refresh_universe(self) -> None:
        self._universe_worker = UniverseWorker(str(self.store.paths.cache), self)
        self._universe_worker.succeeded.connect(self._on_universe)
        self._universe_worker.failed.connect(
            lambda message: QMessageBox.warning(self, "Universe cache", message)
        )
        self._universe_worker.start()
        self.statusBar().showMessage("Downloading the public static data export…")

    def _on_universe(self, meta: dict) -> None:
        self.index = load_best_index(self.store.paths.cache)
        self.load_selected_route()
        self.statusBar().showMessage(
            f"Cached {meta.get('system_count')} systems. "
            f"Skipped {meta.get('skipped_without_english_name', 0)} unnamed records."
        )

    def _reset_auth(self) -> None:
        self._tokens = None
        self._oauth = None

    def _http_client(self) -> HttpClient:
        if self._http is None:
            self._http = HttpClient()
        return self._http

    def _oauth_client(self) -> OAuthClient:
        if self._oauth is None:
            self._oauth = OAuthClient(
                self._http_client(),
                self.settings.client_id,
                self.settings.redirect_uri(),
            )
        return self._oauth

    def _ensure_tokens(self) -> TokenManager:
        if self._tokens is not None:
            return self._tokens
        creds, _kind = open_credential_store(self.store.paths.tokens, self.settings.allow_file_token_store)
        self._creds = creds
        self._tokens = TokenManager(creds, self._oauth_client())
        return self._tokens

    def _sender(self):
        if self.effective_demo():
            return demo_send
        manager = self._ensure_tokens()
        esi = EsiClient(self._http_client())

        def send(character_id: int, destination_id: int, add: bool, clear: bool) -> Outcome:
            try:
                token = manager.access_token(character_id)
                return esi.set_waypoint(
                    token,
                    destination_id,
                    add_to_beginning=add,
                    clear_other_waypoints=clear,
                )
            except RequestTimeout as exc:
                return Outcome(
                    OutcomeKind.UNCERTAIN,
                    f"The request timed out ({exc}). It will not be retried automatically.",
                )
            except NetworkError as exc:
                return Outcome(OutcomeKind.NETWORK, str(exc))
            except (UrlNotAllowed, AuthError) as exc:
                return Outcome(OutcomeKind.FAILED, str(exc))

        return send

    def collect_jobs(self) -> list[ApplyJob]:
        target = "assigned" if self.target_assigned.isChecked() else "selected"
        mode = "append" if self.append_radio.isChecked() else "replace"
        return plan_jobs(
            self.library,
            self.checked_character_ids(),
            target=target,
            mode=mode,
            selected_route_id=self.current_route_id(),
            live=not self.effective_demo(),
        )

    def apply_clicked(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        jobs = self.collect_jobs()
        if not jobs:
            QMessageBox.information(self, "Route Desk", "Check at least one character.")
            return
        summary = describe_plan(jobs, demo=self.effective_demo())
        answer = QMessageBox.question(self, "Set waypoints", summary)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.start_jobs(jobs)

    def start_jobs(self, jobs: list[ApplyJob]) -> None:
        try:
            sender = self._sender()
        except CredentialError as exc:
            QMessageBox.warning(self, "Credential store", str(exc))
            return
        self.jobs = jobs
        self.prepare_progress(jobs)
        self._cancel = CancelToken()
        honor = not self.effective_demo()
        self._worker = ApplyWorker(jobs, sender, self.store, self._cancel, honor_pace=honor, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_apply_finished)
        self._worker.failed.connect(self._on_apply_failed)
        self.apply_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._worker.start()

    def prepare_progress(self, jobs: list[ApplyJob]) -> None:
        rows: list[tuple[ApplyJob, int | None]] = []
        for job in jobs:
            if job.error or not job.stops:
                rows.append((job, None))
            else:
                for index in range(len(job.stops)):
                    rows.append((job, index))
        self.progress.setRowCount(len(rows))
        self._progress_rows = {}
        for row, (job, index) in enumerate(rows):
            self._progress_rows[(job.id, index)] = row
            if index is None:
                values = [job.character_name, job.route_name, "—", "error" if job.error else "—", job.error]
                status = "failed" if job.error else "pending"
            else:
                stop = job.stops[index]
                values = [
                    job.character_name,
                    job.route_name,
                    f"{index + 1}. {stop.destination_name or stop.system_name}",
                    stop.status,
                    stop.message,
                ]
                status = stop.status
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if column == 3:
                    item.setForeground(QColor(STATUS_COLORS.get(status, "#d7d2c8")))
                self.progress.setItem(row, column, item)
        self.progress.resizeColumnsToContents()

    def _paint_job(self, job: ApplyJob) -> None:
        if (job.id, None) in self._progress_rows or not any(key[0] == job.id for key in self._progress_rows):
            self.prepare_progress(self.jobs)
            return
        for index, stop in enumerate(job.stops):
            row = self._progress_rows.get((job.id, index))
            if row is None:
                continue
            result = self.progress.item(row, 3)
            detail = self.progress.item(row, 4)
            if result is None or detail is None:
                continue
            result.setText(stop.status)
            result.setForeground(QColor(STATUS_COLORS.get(stop.status, "#d7d2c8")))
            detail.setText(stop.message)
        self.progress.resizeColumnsToContents()

    def _on_progress(self, payload: dict) -> None:
        job = ApplyJob.from_dict(payload["job"])
        for index, existing in enumerate(self.jobs):
            if existing.id == job.id:
                self.jobs[index] = job
                break
        self._paint_job(job)

    def _on_apply_finished(self, payload: list) -> None:
        self.jobs = [ApplyJob.from_dict(item) for item in payload]
        self.prepare_progress(self.jobs)
        self.apply_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.statusBar().showMessage("Apply finished. Confirmed stops will not be sent again.")

    def _on_apply_failed(self, message: str) -> None:
        self.apply_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        QMessageBox.warning(self, "Apply failed", message)

    def cancel_apply(self) -> None:
        self._cancel.cancel()
        self.statusBar().showMessage(
            "Cancel requested. The stop already in flight may still finish. Later stops will not be sent."
        )

    def retry_apply(self) -> None:
        if not self.jobs:
            self.statusBar().showMessage("Nothing to retry yet.")
            return
        self.start_jobs(self.jobs)

    def resume_apply(self) -> None:
        if not self.jobs:
            return
        for job in self.jobs:
            resume_cancelled(job)
        self.start_jobs(self.jobs)

    def resolve_uncertain(self, applied: bool) -> None:
        changed = False
        for job in self.jobs:
            changed = mark_uncertain(job, applied=applied) or changed
        if not changed:
            self.statusBar().showMessage("No uncertain stop is waiting.")
            return
        try:
            self.store.save_jobs(self.jobs)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
        self.prepare_progress(self.jobs)
        if not applied:
            self.start_jobs(self.jobs)

    def import_routes_dialog(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "Import routes", "", "JSON (*.json)")
        if path:
            self.import_routes_from_path(Path(path))

    def export_routes_dialog(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self, "Export routes", "routes.json", "JSON (*.json)")
        if path:
            self.export_routes_to_path(Path(path))

    def import_routes_from_path(self, path: Path) -> None:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            routes = import_routes(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Import", str(exc))
            return
        self.library.routes.extend(routes)
        self._save_library()
        self.refresh_routes()
        self.statusBar().showMessage(f"Imported {len(routes)} route(s). Nothing already saved was removed.")

    def export_routes_to_path(self, path: Path) -> None:
        try:
            text = json.dumps(export_routes(self.library.routes), indent=2, ensure_ascii=False)
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Export", str(exc))
            return
        self.statusBar().showMessage(f"Exported {len(self.library.routes)} route(s). Tokens are not included.")

    def progress_text(self) -> str:
        chunks = []
        for row in range(self.progress.rowCount()):
            chunks.append(
                " | ".join(
                    self.progress.item(row, column).text() if self.progress.item(row, column) else ""
                    for column in range(self.progress.columnCount())
                )
            )
        return "\n".join(chunks)

    def dump_widget_text(self) -> str:
        parts = [self.windowTitle(), self.banner.text(), self.storage_note.text(), self.contract_note.text()]
        for button in self.findChildren(QPushButton):
            parts.append(button.text())
        for radio in self.findChildren(QRadioButton):
            parts.append(radio.text())
        return "\n".join(parts)

    def capture_story(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.resize(1440, 980)
        self.show()
        self._pump(400)
        if self.library.route_by_name("Jita - Ignoitton - Siseide"):
            self.select_route_by_name("Jita - Ignoitton - Siseide")
        self.check_all_characters()
        self._pump(300)
        if self.destination_combo.count() > 1:
            self.destination_combo.setCurrentIndex(1)
        self.grab().save(str(directory / "route-desk-routes.png"))
        self.grab().save(str(directory / "route-desk-stations.png"))
        if self.library.route_by_name("Siseide - Ignoitton - Jita"):
            self.select_route_by_name("Siseide - Ignoitton - Jita")
            self._pump(250)
            self.grab().save(str(directory / "route-desk-second-route.png"))
        self.select_route_by_name("Jita - Ignoitton - Siseide")
        self.set_target("selected")
        self.set_waypoint_mode("replace")
        alpha = next((c for c in self.library.characters if c.character_name == "Demo Pilot Alpha"), None)
        if alpha is not None:
            self.check_only([alpha.character_id])
        jobs = self.collect_jobs()
        self.jobs = jobs
        self.prepare_progress(jobs)

        def paint(job, _index):
            self._paint_job(job)
            QApplication.processEvents()

        execute_jobs(jobs, demo_send, on_progress=paint)
        self.store.save_jobs(jobs)
        self._pump(200)
        self.grab().save(str(directory / "route-desk-progress.png"))
        self._add_long_demo_route()
        self._pump(300)
        self.grab().save(str(directory / "route-desk-long-list.png"))

    def _add_long_demo_route(self) -> None:
        systems = list(self.index.systems)
        route = Route(
            id=new_id(),
            name="Long editor list",
            notes="Editor demonstration of a long list. Not a suggested route and not a jump check.",
        )
        if len(systems) >= 40:
            chosen = systems[:40]
        else:
            from everoute.universe import SolarSystem

            chosen = [SolarSystem(30_000_000 + i, f"Example {i:02d}", None, "", None) for i in range(40)]
        for index, system in enumerate(chosen):
            route.stops.append(
                Stop(
                    system_id=system.system_id,
                    system_name=system.name,
                    leg_type=LEG_ORIGIN if index == 0 else "gate",
                    security=system.security,
                    region_name=system.region_name,
                )
            )
        touch(route)
        self.library.routes.append(route)
        self._save_library()
        self.refresh_routes()
        self.select_route_by_name(route.name)
        self.stop_table.scrollToBottom()

    def _pump(self, milliseconds: int) -> None:
        end = time.time() + milliseconds / 1000
        while time.time() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._cancel.cancel()
            self._worker.wait(3000)
        self._cancel_login()
        self._save_library()
        event.accept()


def _band(security: float) -> str:
    if security >= 0.45:
        return "high"
    if security > 0:
        return "low"
    return "null"
