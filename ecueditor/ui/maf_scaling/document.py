from __future__ import annotations

from typing import cast

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QFont, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ecueditor.core.maf_scaling import (
    ELECTRICAL_PRESETS_OHMS,
    MafRecord,
    MafPreview,
    ScalingRequest,
    build_maf_preview,
    get_maf,
    list_mafs,
    maf_voltage_axes,
    shape_maf_values,
    table_maf_record,
)
from ecueditor.core.maf_scaling.models import DiameterUnit
from ecueditor.core.mapstudio import MapValidationError, fingerprint_table
from ecueditor.ui.mapstudio.widgets import (
    ArrayLegend,
    ArrayTableWidget,
    TableZoomControls,
    content_sized_document_hint,
)
from ecueditor.ui.workspace.status_chips import Chip


class MafScalingDocument(QWidget):
    """Snapshot-based MAF scaler hosted inside the tuning workspace."""

    applyRequested = Signal(object)
    openTableRequested = Signal(object)

    def __init__(
        self,
        rom,
        table,
        *,
        manual_override: bool = False,
        display_settings=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.rom = rom
        self.table = table
        self.manual_override = manual_override
        self._mode_table = rom.tables.get("MAF Mode - 2048 kg/hr")
        self.preview: MafPreview | None = None
        self._fingerprint = fingerprint_table(table)
        self._mode_fingerprint = self._current_mode_fingerprint()
        self._mode_compatible = True
        self._stale = False
        self._building = True
        self._initial_fit_pending = True
        self._colormap = getattr(display_settings, "colormap", "rainbow")
        self._build_ui(display_settings)
        self._building = False
        self._source_changed()
        self._preset_changed()

    def _build_ui(self, display_settings) -> None:
        from ecueditor.ui.design.icons import icon

        self.setObjectName("mapStudioDocument")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("mapStudioHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 14, 8)
        header_layout.setSpacing(8)
        heading = QVBoxLayout()
        heading.setSpacing(1)
        title = QLabel(self.table.name)
        title.setObjectName("frameTitle")
        title_font = QFont(self.font())
        title_font.setPointSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        subtitle = QLabel("MAF Scaling · catalog transfer-function workspace")
        subtitle.setObjectName("mapStudioSubtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header_layout.addLayout(heading, 1)
        header_layout.addWidget(Chip("MAF SCALING", "accent"))
        header_layout.addWidget(
            Chip(
                "MANUAL TARGET" if self.manual_override else "DETECTED TARGET",
                "warn" if self.manual_override else "ok",
            )
        )
        root.addWidget(header)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("mapStudioSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)

        workspace = QWidget(self.splitter)
        workspace.setObjectName("mapStudioWorkspace")
        workspace.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(10, 9, 9, 9)
        workspace_layout.setSpacing(6)
        panel_title = QLabel("Source and destination preview")
        panel_title.setObjectName("mapStudioPanelTitle")
        workspace_layout.addWidget(panel_title)
        self.tabs = QTabWidget(workspace)
        self.tabs.setObjectName("mapStudioTabs")
        self.source_table = ArrayTableWidget(colormap=self._colormap)
        self.result_table = ArrayTableWidget(colormap=self._colormap)
        self.changes_table = ArrayTableWidget(colormap=self._colormap)
        self.source_legend = ArrayLegend()
        self.result_legend = ArrayLegend()
        self.changes_legend = ArrayLegend()
        self.source_legend.set_table(self.source_table)
        self.result_legend.set_table(self.result_table)
        self.changes_legend.set_table(self.changes_table)
        if display_settings is not None:
            for table in (self.source_table, self.result_table, self.changes_table):
                table.configure_display(
                    font_size=int(getattr(display_settings, "font_size", 11)),
                    density=str(getattr(display_settings, "table_density", "normal")),
                    color_cells=bool(getattr(display_settings, "color_cells", True)),
                )
        self.tabs.addTab(self._table_page(self.source_table, self.source_legend), "Source")
        self.tabs.addTab(self._table_page(self.result_table, self.result_legend), "Result")
        self.tabs.addTab(self._table_page(self.changes_table, self.changes_legend), "Changes")
        self.tabs.setTabEnabled(1, False)
        self.tabs.setTabEnabled(2, False)
        workspace_layout.addWidget(self.tabs, 1)
        self.splitter.addWidget(workspace)

        self.inspector_scroll = QScrollArea(self.splitter)
        self.inspector_scroll.setObjectName("mapStudioInspectorScroll")
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inspector_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.inspector_scroll.setMinimumWidth(350)
        self.inspector_scroll.setMaximumWidth(420)
        inspector = QWidget()
        inspector.setObjectName("mapStudioInspector")
        inspector.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inspector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(10, 10, 10, 12)
        inspector_layout.setSpacing(10)

        destination_group = QGroupBox("Destination table", inspector)
        destination_layout = QVBoxLayout(destination_group)
        sx, sy = self.table.shape()
        units = self.table.cells[0].scale.units or "scaled units"
        self.destination_label = QLabel(f"{self.table.name} · {sx} × {sy} · {units}")
        self.destination_label.setWordWrap(True)
        destination_layout.addWidget(self.destination_label)
        destination_help = QLabel(
            "The definition controls shape, engineering units, raw conversion, and storage limits."
        )
        destination_help.setObjectName("mapStudioHelp")
        destination_help.setWordWrap(True)
        destination_layout.addWidget(destination_help)
        self.mode_label = QLabel()
        self.mode_label.setObjectName("mapStudioHelp")
        self.mode_label.setWordWrap(True)
        destination_layout.addWidget(self.mode_label)
        self.open_mode_button = QPushButton("Open MAF Mode Table")
        self.open_mode_button.setIcon(icon("switch"))
        self.open_mode_button.setVisible(self._mode_table is not None)
        destination_layout.addWidget(self.open_mode_button)
        inspector_layout.addWidget(destination_group)

        source_group = QGroupBox("1 · Source MAF", inspector)
        source_layout = QVBoxLayout(source_group)
        self.source_combo = QComboBox(source_group)
        self.source_combo.addItem("Current table values", None)
        for record in list_mafs():
            self.source_combo.addItem(record.display_name, record.id)
        source_layout.addWidget(self.source_combo)
        self.source_details = QLabel()
        self.source_details.setObjectName("mapStudioHelp")
        self.source_details.setWordWrap(True)
        source_layout.addWidget(self.source_details)
        source_form = QFormLayout()
        self.source_diameter_box = self._number_box(0.0, 10000.0, 3)
        self.target_diameter_box = self._number_box(0.0, 10000.0, 3)
        self.source_diameter_box.setSingleStep(0.25)
        self.target_diameter_box.setSingleStep(0.25)
        self.diameter_unit_combo = QComboBox()
        self.diameter_unit_combo.addItem("inch", "inch")
        self.diameter_unit_combo.addItem("mm", "mm")
        source_form.addRow("Source inside diameter", self.source_diameter_box)
        source_form.addRow("Target inside diameter", self.target_diameter_box)
        source_form.addRow("Diameter unit", self.diameter_unit_combo)
        source_layout.addLayout(source_form)
        inspector_layout.addWidget(source_group)

        electrical_group = QGroupBox("2 · ECU electrical model", inspector)
        electrical_form = QFormLayout(electrical_group)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("MS41", "MS41")
        self.preset_combo.addItem("MS43", "MS43")
        self.preset_combo.addItem("Custom", None)
        self.pullup_box = self._number_box(0.0, 1_000_000.0, 1)
        self.series_box = self._number_box(0.0, 1_000_000.0, 1)
        electrical_form.addRow("ECU preset", self.preset_combo)
        electrical_form.addRow("Pull-up resistance (Ω)", self.pullup_box)
        electrical_form.addRow("Series resistance (Ω)", self.series_box)
        inspector_layout.addWidget(electrical_group)

        options_group = QGroupBox("3 · Output policy", inspector)
        options_layout = QVBoxLayout(options_group)
        self.floor_negative_check = QCheckBox("Floor negative output values to 0")
        self.floor_negative_check.setChecked(True)
        self.floor_negative_check.setToolTip(
            "Enabled by default so negative flow values are not sent to the destination. "
            "Clear only when preserving them is intentional."
        )
        options_layout.addWidget(self.floor_negative_check)
        self.generate_button = QPushButton("Generate Preview")
        self.generate_button.setIcon(icon("interpolate"))
        options_layout.addWidget(self.generate_button)
        inspector_layout.addWidget(options_group)
        inspector_layout.addStretch(1)
        self.inspector_scroll.setWidget(inspector)
        self.splitter.addWidget(self.inspector_scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([720, 350])
        root.addWidget(self.splitter, 1)

        footer = QWidget(self)
        footer.setObjectName("mapStudioFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 6, 12, 7)
        footer_layout.setSpacing(7)
        self.status_chip = Chip("READY", "neutral")
        self.status_label = QLabel("Enter the tube dimensions, then generate a preview.")
        self.status_label.setObjectName("mapStudioStatus")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.reload_button = QPushButton("Reload Source")
        self.reload_button.setIcon(icon("refresh"))
        self.apply_button = QPushButton(f"Apply to {self.table.name}")
        self.apply_button.setIcon(icon("check"))
        self.apply_button.setProperty("buttonRole", "primary")
        self.apply_button.setEnabled(False)
        footer_layout.addWidget(self.status_chip)
        footer_layout.addWidget(self.status_label, 1)
        footer_layout.addWidget(self.reload_button)
        footer_layout.addWidget(self.apply_button)
        root.addWidget(footer)

        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        self.generate_button.clicked.connect(self.generate_preview)
        self.reload_button.clicked.connect(self.reload_source)
        self.apply_button.clicked.connect(self.request_apply)
        self.tabs.currentChanged.connect(self._fit_active_preview)
        self.open_mode_button.clicked.connect(
            lambda: self.openTableRequested.emit(self._mode_table)
        )
        for signal in (
            self.source_diameter_box.valueChanged,
            self.target_diameter_box.valueChanged,
            self.diameter_unit_combo.currentIndexChanged,
            self.pullup_box.valueChanged,
            self.series_box.valueChanged,
            self.floor_negative_check.toggled,
        ):
            signal.connect(self._inputs_changed)
        self._refresh_mode_status()

    @staticmethod
    def _number_box(minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(decimals)
        box.setKeyboardTracking(False)
        return box

    @staticmethod
    def _table_page(table: ArrayTableWidget, legend: ArrayLegend) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(table, 1)
        footer = QWidget(page)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(6, 3, 6, 0)
        footer_layout.addWidget(legend, 1)
        footer_layout.addWidget(TableZoomControls(table))
        layout.addWidget(footer)
        return page

    def sizeHint(self) -> QSize:
        return content_sized_document_hint(self, self.splitter, self.inspector_scroll)

    def minimumSizeHint(self) -> QSize:
        return QSize(680, 420)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._initial_fit_pending:
            self._initial_fit_pending = False
            self._fit_active_preview(self.tabs.currentIndex())

    def _selected_source(self) -> MafRecord:
        source_id = self.source_combo.currentData()
        return table_maf_record(self.table) if source_id is None else get_maf(str(source_id))

    def _refresh_source_table(self, record: MafRecord | None = None) -> None:
        selected = self._selected_source() if record is None else record
        x_axis, y_axis = maf_voltage_axes(self.table)
        self.source_table.set_values(
            shape_maf_values(self.table, selected.flow_values_kg_per_hr),
            x=x_axis,
            y=y_axis,
            editable=False,
            decimals=self.table.cells[0].scale.decimals(),
        )
        self.source_legend.refresh()
        if self.isVisible() and self.tabs.currentIndex() == 0:
            self._fit_active_preview(0)

    def _source_changed(self, *_args) -> None:
        source_id = self.source_combo.currentData()
        if source_id is None:
            record = table_maf_record(self.table)
            self._set_default_diameters(record.default_tube_diameter_in)
            self.source_details.setText(
                "Uses a snapshot of the opening table's 256 definition-scaled values as kg/hr."
            )
            self._refresh_source_table(record)
            self._inputs_changed()
            return
        record = get_maf(str(source_id))
        details = [
            part for part in (record.manufacturer, record.part_number, record.variant) if part
        ]
        diameter = record.source_tube_diameter
        if diameter.source_text:
            details.append(f"Catalog diameter: {diameter.source_text}")
        if record.uncertainty:
            details.append("Review catalog uncertainty before applying.")
        self.source_details.setText(" · ".join(details) or record.source_header)
        self._set_default_diameters(record.default_tube_diameter_in)
        self._refresh_source_table(record)
        self._inputs_changed()

    def _set_default_diameters(self, diameter_in: float) -> None:
        self.diameter_unit_combo.setCurrentIndex(
            self.diameter_unit_combo.findData("inch")
        )
        self.source_diameter_box.setValue(diameter_in)
        self.target_diameter_box.setValue(diameter_in)

    def refresh_catalog(self) -> None:
        selected_id = self.source_combo.currentData()
        blocker = QSignalBlocker(self.source_combo)
        self.source_combo.clear()
        self.source_combo.addItem("Current table values", None)
        for record in list_mafs():
            self.source_combo.addItem(record.display_name, record.id)
        index = self.source_combo.findData(selected_id)
        self.source_combo.setCurrentIndex(index if index >= 0 else 0)
        del blocker
        self._source_changed()

    def _preset_changed(self, *_args) -> None:
        preset = self.preset_combo.currentData()
        custom = preset is None
        self.pullup_box.setEnabled(custom)
        if not custom:
            self.pullup_box.setValue(ELECTRICAL_PRESETS_OHMS[str(preset)])
        self._inputs_changed()

    def _inputs_changed(self, *_args) -> None:
        if self._building or self.preview is None:
            return
        self._clear_preview()
        self.status_chip.setText("REGENERATE")
        self.status_chip.set_kind("warn")
        self.status_label.setText("Scaling inputs changed. Generate a new preview.")

    def _clear_preview(self) -> None:
        self.preview = None
        self.result_table.clear()
        self.changes_table.clear()
        self.tabs.setTabEnabled(1, False)
        self.tabs.setTabEnabled(2, False)
        self.tabs.setCurrentIndex(0)
        self.apply_button.setEnabled(False)
        self.result_legend.refresh()
        self.changes_legend.refresh()

    def _expected_mode(self) -> str | None:
        name = self.table.name.casefold()
        if "2048" in name:
            return "2048"
        if "1024" in name:
            return "1024"
        return None

    def _current_mode_fingerprint(self) -> tuple[object, ...] | None:
        if self._mode_table is None:
            return None
        return fingerprint_table(self._mode_table)

    def _refresh_mode_status(self) -> None:
        expected = self._expected_mode()
        if expected is None:
            if self._mode_table is None:
                self.mode_label.setText(
                    "MAF mode: this destination name does not declare a separate 1024/2048 mode."
                )
            else:
                state = getattr(self._mode_table, "active_state", lambda: None)()
                self.mode_label.setText(
                    f"MAF mode switch: {state or 'unknown'}; verify it for this destination."
                )
            self._mode_compatible = True
            return
        if self._mode_table is None:
            self.mode_label.setText(
                f"{expected} kg/hr MAF mode cannot be verified from this definition."
            )
            self._mode_compatible = True
            return

        state = getattr(self._mode_table, "active_state", lambda: None)()
        if state is None:
            self.mode_label.setText(
                f"{expected} kg/hr MAF mode cannot be verified: the mode switch state is unknown."
            )
            self._mode_compatible = True
            return
        words = {
            word.strip("()[]{}.,:;") for word in str(state).casefold().replace("kg/hr", "").split()
        }
        enabled_2048 = bool(words & {"2048", "enabled", "on"})
        disabled_2048 = bool(words & {"1024", "disabled", "off"})
        matches = enabled_2048 if expected == "2048" else disabled_2048
        opposes = disabled_2048 if expected == "2048" else enabled_2048
        if matches:
            self.mode_label.setText(
                f"MAF mode verified: {state} matches this {expected} kg/hr destination."
            )
            self._mode_compatible = True
        elif opposes:
            self.mode_label.setText(
                f"MAF mode mismatch: switch is {state}, destination expects {expected} kg/hr."
            )
            self._mode_compatible = False
        else:
            self.mode_label.setText(
                f"{expected} kg/hr MAF mode cannot be verified from switch state {state!r}."
            )
            self._mode_compatible = True

    def generate_preview(self) -> bool:
        try:
            self._refresh_mode_status()
            if not self._mode_compatible:
                raise ValueError(self.mode_label.text())
            source_diameter = self.source_diameter_box.value()
            target_diameter = self.target_diameter_box.value()
            if source_diameter <= 0 or target_diameter <= 0:
                raise ValueError("Enter positive source and target inside diameters.")
            preset_data = self.preset_combo.currentData()
            preset = str(preset_data) if preset_data is not None else None
            pullup = None
            if preset is None:
                pullup = self.pullup_box.value()
                if pullup <= 0:
                    raise ValueError("Enter a positive custom pull-up resistance.")
            source = self._selected_source()
            request = ScalingRequest(
                source=source,
                source_tube_diameter=source_diameter,
                target_tube_diameter=target_diameter,
                diameter_unit=cast(DiameterUnit, str(self.diameter_unit_combo.currentData())),
                ecu_preset=preset,
                pullup_resistance_ohms=pullup,
                series_resistance_ohms=self.series_box.value(),
            )
            preview = build_maf_preview(
                self.table,
                request,
                floor_negative=self.floor_negative_check.isChecked(),
            )
        except (MapValidationError, ValueError, KeyError) as exc:
            return self._error(str(exc))

        self.preview = preview
        self._fingerprint = fingerprint_table(self.table)
        self._mode_fingerprint = self._current_mode_fingerprint()
        self._stale = False
        self._refresh_source_table(source)
        decimals = self.table.cells[0].scale.decimals()
        x_axis, y_axis = maf_voltage_axes(self.table)
        self.result_table.set_values(
            preview.proposal.values,
            x=x_axis,
            y=y_axis,
            editable=False,
            decimals=decimals,
        )
        self.changes_table.set_values(
            preview.changes,
            x=x_axis,
            y=y_axis,
            editable=False,
            decimals=decimals,
            difference=True,
        )
        self.result_legend.refresh()
        self.changes_legend.refresh()
        self.tabs.setTabEnabled(1, True)
        self.tabs.setTabEnabled(2, True)
        self.apply_button.setEnabled(True)
        self.tabs.setCurrentIndex(1)
        notes = [
            f"Preview fits {preview.destination_min:g} to {preview.destination_max:g} destination units."
        ]
        if preview.floored_count:
            notes.append(f"Floored {preview.floored_count} negative samples.")
        notes.extend(preview.result.warnings)
        self.status_label.setText(" ".join(notes))
        self.status_chip.setText("PREVIEW")
        self.status_chip.set_kind("accent")
        return True

    def _fit_active_preview(self, index: int) -> None:
        tables = (self.source_table, self.result_table, self.changes_table)
        if 0 <= index < len(tables):
            table = tables[index]
            next(
                control
                for control in self.findChildren(TableZoomControls)
                if control.table is table
            ).fit_after_layout()

    def is_stale(self) -> bool:
        return (
            fingerprint_table(self.table) != self._fingerprint
            or self._current_mode_fingerprint() != self._mode_fingerprint
        )

    def refresh_stale_state(self) -> None:
        self._refresh_mode_status()
        self._stale = self.is_stale()
        self.apply_button.setEnabled(
            self.preview is not None and not self._stale and self._mode_compatible
        )
        if self._stale:
            self.status_chip.setText("STALE")
            self.status_chip.set_kind("warn")
            self.status_label.setText(
                "The destination table changed after this preview was generated. Reload before applying."
            )

    def handle_rom_reloaded(self) -> None:
        if self.preview is None:
            self._fingerprint = fingerprint_table(self.table)
            self._mode_fingerprint = self._current_mode_fingerprint()
            self._stale = False
            self._refresh_mode_status()
            self._refresh_source_table()
            return
        self.refresh_stale_state()

    def refresh_dependency_state(self) -> None:
        self.refresh_stale_state()

    def request_apply(self) -> None:
        self.refresh_stale_state()
        if self.preview is None or self._stale:
            return
        self.applyRequested.emit(self.preview.proposal)

    def accept_applied(self, *, changed: bool = True) -> None:
        self._fingerprint = fingerprint_table(self.table)
        self._mode_fingerprint = self._current_mode_fingerprint()
        self._stale = False
        self._clear_preview()
        self._refresh_source_table()
        if changed:
            self.status_label.setText("Applied to the destination table as one undoable operation.")
            self.status_chip.setText("APPLIED")
        else:
            self.status_label.setText(
                "The preview already matches the destination table; no undo operation was created."
            )
            self.status_chip.setText("NO CHANGE")
        self.status_chip.set_kind("ok")

    def has_local_changes(self) -> bool:
        return self.preview is not None

    def can_close(self) -> bool:
        if not self.has_local_changes():
            return True
        return (
            QMessageBox.question(
                self,
                "Discard MAF scaling preview?",
                "The generated preview has not been applied to the destination table.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def reload_source(self) -> None:
        if self.preview is not None:
            answer = QMessageBox.question(
                self,
                "Discard MAF scaling preview?",
                "Reloading discards the generated preview. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._clear_preview()
        self._fingerprint = fingerprint_table(self.table)
        self._mode_fingerprint = self._current_mode_fingerprint()
        self._stale = False
        self._refresh_mode_status()
        self._refresh_source_table()
        self.status_chip.setText("READY")
        self.status_chip.set_kind("neutral")
        self.status_label.setText("Destination reloaded. Generate a new preview.")

    def _error(self, message: str) -> bool:
        self.apply_button.setEnabled(False)
        self.status_label.setText(message)
        self.status_chip.setText("CHECK INPUT")
        self.status_chip.set_kind("warn")
        return False
