"""Settings dialog and the Anlas warning."""
from ..qt import (QDialog, QFormLayout, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
                  QCheckBox, QComboBox, QSpinBox, QLabel, QDialogButtonBox, QFileDialog,
                  QMessageBox, PASSWORD, DBB_OK, DBB_CANCEL, MB_WARNING, ACCEPT_ROLE,
                  REJECT_ROLE, exec_dialog)


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NovelAI settings")
        self.settings = settings
        form = QFormLayout()

        self.token = QLineEdit(settings.token)
        self.token.setEchoMode(PASSWORD)
        self.token.setPlaceholderText("pst-...  (NovelAI > Account Settings > Get Persistent API Token)")
        form.addRow("API token", self.token)

        row = QHBoxLayout()
        self.autosave = QLineEdit(settings.autosave_dir)
        self.autosave.setPlaceholderText("Folder for raw NovelAI PNGs (empty = off)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.autosave)
        row.addWidget(browse)
        form.addRow("Autosave folder", row)

        self.style = QComboBox()
        self.style.addItems([s.name for s in settings.styles])
        self.style.setCurrentText(settings.default_style)
        form.addRow("Default style", self.style)

        self.free_only = QCheckBox("Free generations only (block anything that costs Anlas)")
        self.free_only.setChecked(settings.free_only)
        form.addRow(self.free_only)

        self.panels = QCheckBox("Read panel names from the layer stack (also on the docker)")
        self.panels.setChecked(settings.read_panel_names)
        form.addRow(self.panels)

        self.tag_complete = QCheckBox("Suggest Danbooru tags while typing prompts")
        self.tag_complete.setChecked(settings.tag_autocomplete)
        form.addRow(self.tag_complete)

        self.multi_mode = QComboBox()
        self.multi_mode.addItem("Queue: one request per image (free on Opus)", "queue")
        self.multi_mode.addItem("Batch: one request for all (always costs Anlas)", "batch")
        self.multi_mode.setCurrentIndex(max(0, self.multi_mode.findData(settings.multi_mode)))
        form.addRow("Several images", self.multi_mode)

        self.timeout = QSpinBox()
        self.timeout.setRange(30, 600)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(settings.timeout)
        form.addRow("Request timeout", self.timeout)

        note = QLabel("Styles are stored in nai_krita_settings.json in Krita's resource folder "
                      "and can be edited there while Krita is closed.")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(DBB_OK | DBB_CANCEL)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Autosave folder", self.autosave.text())
        if folder:
            self.autosave.setText(folder)

    def apply_to(self, settings):
        settings.token = self.token.text().strip()
        settings.autosave_dir = self.autosave.text().strip()
        settings.default_style = self.style.currentText()
        settings.free_only = self.free_only.isChecked()
        settings.read_panel_names = self.panels.isChecked()
        settings.tag_autocomplete = self.tag_complete.isChecked()
        settings.multi_mode = self.multi_mode.currentData() or "queue"
        settings.timeout = self.timeout.value()


def confirm_paid(parent, verdict, balance, requests: int, session_state: dict) -> bool:
    """Ask before spending Anlas, once for a whole Generate click. Cancel is the
    default. `verdict` is the combined verdict for all requests in the click."""
    if session_state.get("dont_ask"):
        return True
    box = QMessageBox(parent)
    box.setIcon(MB_WARNING)
    box.setWindowTitle("This will cost Anlas")
    bal = f"{balance.fixed:,} subscription + {balance.purchased:,} paid" if balance else "unknown"
    reasons = "\n".join(f"• {r}" for r in verdict.reasons)
    what = "These requests are" if requests > 1 else "This request is"
    box.setText(f"{what} not free:\n{reasons}")
    box.setInformativeText(
        f"Estimated cost: about {verdict.estimate:,} Anlas"
        + (f" in total for {requests} requests" if requests > 1 else "")
        + f"\nCurrent balance: {bal}\n\nThe estimate is approximate.")
    spend = box.addButton("Spend Anlas", ACCEPT_ROLE)
    cancel = box.addButton("Cancel", REJECT_ROLE)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    check = QCheckBox("Don't ask again this session")
    box.setCheckBox(check)
    exec_dialog(box)
    ok = box.clickedButton() is spend
    if ok and check.isChecked():
        session_state["dont_ask"] = True
    return ok


def confirm_import(parent, title: str, will_set: list, not_set: list) -> bool:
    """Show what an image import will change before it replaces the prompt."""
    box = QMessageBox(parent)
    box.setWindowTitle("Import settings")
    box.setText(title)
    text = "This replaces:\n" + "\n".join(f"• {s}" for s in will_set)
    if not_set:
        text += "\n\nNot imported:\n" + "\n".join(f"• {s}" for s in not_set)
    box.setInformativeText(text)
    ok = box.addButton("Import", ACCEPT_ROLE)
    cancel = box.addButton("Cancel", REJECT_ROLE)
    box.setDefaultButton(ok)
    box.setEscapeButton(cancel)
    exec_dialog(box)
    return box.clickedButton() is ok
