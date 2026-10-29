from __future__ import annotations

import math
import random
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum, auto

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFont, QGuiApplication, QScreen
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


APP_STYLE = """
QWidget#reminder, QWidget#controls { background: #f7faf9; color: #23332d; }
QWidget#reminder { border: 1px solid #b9cec4; }
QLabel { color: #23332d; background: transparent; border: none; }
QLabel#eyebrow { color: #487563; font-size: 12px; font-weight: 600; }
QLabel#question { font-size: 20px; font-weight: 600; }
QLabel#detail { color: #556b60; font-size: 12px; }
QLabel#notice { color: #944a22; }
QPushButton { min-height: 34px; padding: 0 12px; border-radius: 5px;
              border: 1px solid #b9cec4; background: #ffffff; color: #23332d; }
QPushButton#iconButton { padding: 0; }
QPushButton:hover { background: #e7f0eb; }
QPushButton:pressed { background: #d3e6dc; }
QPushButton:disabled { color: #7d8782; background: #edf0ee; border-color: #d9e0dc; }
QPushButton#primary { background: #217554; color: #ffffff; border-color: #217554; }
QPushButton#primary:hover { background: #185f43; }
QPushButton#primary:disabled { background: #dce6e0; color: #77877e; border-color: #dce6e0; }
"""


def range_text(lower: float, upper: float) -> str:
    return f"{lower:.1f} - {upper:.1f} seconds"


@dataclass(frozen=True)
class TimerSettings:
    minimum: float = 8.0
    maximum: float = 12.0
    increase_percent: float = 5.0
    cap: float = 120.0

    def __post_init__(self) -> None:
        values = (self.minimum, self.maximum, self.increase_percent, self.cap)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("All settings must be finite numbers.")
        if not 1 <= self.minimum < self.maximum <= self.cap <= 86400:
            raise ValueError("Use 1 <= minimum < maximum <= cap <= 86,400 seconds.")
        if not 0 < self.increase_percent <= 100:
            raise ValueError("The increase must be greater than 0% and at most 100%.")


class SessionState(Enum):
    PAUSED = auto()
    RUNNING = auto()
    AWAITING_ANSWER = auto()


class TrainingSession:
    def __init__(
        self,
        settings: TimerSettings | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings if settings is not None else TimerSettings()
        self.rng = rng if rng is not None else random.Random()
        self.clock = clock
        self.state = SessionState.PAUSED
        self.lower = self.settings.minimum
        self.upper = self.settings.maximum
        self.deadline: float | None = None

    @property
    def remaining_seconds(self) -> float:
        if self.deadline is None:
            return 0.0
        return max(0.0, self.deadline - self.clock())

    def _start_wait(self) -> None:
        self.deadline = self.clock() + self.rng.uniform(self.lower, self.upper)
        self.state = SessionState.RUNNING

    def start_or_resume(self) -> bool:
        if self.state is not SessionState.PAUSED:
            return False
        self._start_wait()
        return True

    def expire(self) -> bool:
        if self.state is not SessionState.RUNNING or self.remaining_seconds > 0:
            return False
        self.state = SessionState.AWAITING_ANSWER
        self.deadline = None
        return True

    def answer(self, settled: bool) -> bool:
        if self.state is not SessionState.AWAITING_ANSWER:
            return False
        if settled:
            next_upper = min(self.settings.cap, self.upper * (1 + self.settings.increase_percent / 100))
            self.lower *= next_upper / self.upper
            self.upper = next_upper
            self._start_wait()
        else:
            self.pause()
        return True

    def pause(self) -> None:
        self.state = SessionState.PAUSED
        self.deadline = None

    def reset(self, settings: TimerSettings | None = None) -> None:
        if settings is not None:
            self.settings = settings
        self.lower = self.settings.minimum
        self.upper = self.settings.maximum
        self.pause()


class ReminderWindow(QWidget):
    answered = Signal(bool)
    dismissed = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("reminder")
        self.setWindowTitle("Puppy Timer")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        eyebrow = QLabel("PUPPY TIMER")
        eyebrow.setObjectName("eyebrow")
        question = QLabel("Is she still settled?")
        question.setObjectName("question")
        question.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setObjectName("detail")
        self.detail.setWordWrap(True)
        layout.addWidget(eyebrow)
        layout.addWidget(question)
        layout.addWidget(self.detail)

        actions = QHBoxLayout()
        self.yes_button = QPushButton("Yes")
        self.yes_button.setObjectName("primary")
        self.yes_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        self.yes_button.setToolTip("Increase the interval and start the next wait")
        self.no_button = QPushButton("No")
        self.no_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.no_button.setToolTip("Pause and keep the interval unchanged")
        for button in (self.yes_button, self.no_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            actions.addWidget(button, 1)
        self.yes_button.clicked.connect(lambda: self.answered.emit(True))
        self.no_button.clicked.connect(lambda: self.answered.emit(False))
        layout.addLayout(actions)

    def reposition(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 15, area.bottom() - self.height() - 15)

    def show_prompt(self, lower: float, upper: float) -> None:
        self.detail.setText(f"Current range: {range_text(lower, upper)}")
        self.adjustSize()
        self.reposition()
        self.show()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.dismissed.emit()
        super().closeEvent(event)


def load_settings(storage: QSettings) -> tuple[TimerSettings, str]:
    defaults = TimerSettings()
    try:
        values = {key: float(storage.value(key, default)) for key, default in asdict(defaults).items()}
        result = TimerSettings(**values)
    except (TypeError, ValueError, OverflowError):
        return defaults, "Saved settings were invalid. Default settings are in use."
    if storage.status() != QSettings.Status.NoError:
        return defaults, "Saved settings could not be read. Default settings are in use."
    return result, ""


def save_settings(storage: QSettings, settings: TimerSettings) -> bool:
    for key, value in asdict(settings).items():
        storage.setValue(key, value)
    storage.sync()
    return storage.status() == QSettings.Status.NoError


class SettingsDialog(QWidget):
    start_requested = Signal()
    pause_requested = Signal()
    reset_requested = Signal()
    quit_requested = Signal()
    settings_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("controls")
        self.setWindowTitle("Puppy Timer")
        self.setFixedWidth(440)
        self.can_hide = True
        self.settings = TimerSettings()
        self.paused = True
        self.dirty = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        eyebrow = QLabel("PUPPY TIMER")
        eyebrow.setObjectName("eyebrow")
        self.status_label = QLabel("Paused")
        self.status_label.setObjectName("question")
        self.range_label = QLabel()
        self.range_label.setObjectName("detail")
        self.range_label.setWordWrap(True)
        layout.addWidget(eyebrow)
        layout.addWidget(self.status_label)
        layout.addWidget(self.range_label)

        actions = QHBoxLayout()
        self.start_button = QPushButton("Start / Resume")
        self.start_button.setObjectName("primary")
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.pause_button = QPushButton()
        self.pause_button.setObjectName("iconButton")
        self.pause_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.pause_button.setToolTip("Pause")
        self.pause_button.setAccessibleName("Pause")
        self.pause_button.setFixedWidth(40)
        self.reset_button = QPushButton()
        self.reset_button.setObjectName("iconButton")
        self.reset_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.reset_button.setToolTip("Reset to starting range and pause")
        self.reset_button.setAccessibleName("Reset")
        self.reset_button.setFixedWidth(40)
        actions.addWidget(self.start_button, 1)
        actions.addWidget(self.pause_button)
        actions.addWidget(self.reset_button)
        layout.addLayout(actions)

        heading = QLabel("INTERVAL SETTINGS")
        heading.setObjectName("eyebrow")
        layout.addSpacing(6)
        layout.addWidget(heading)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.inputs: dict[str, QDoubleSpinBox] = {}
        for key, label, minimum, maximum, suffix in (
            ("minimum", "Starting minimum", 1.0, 86400.0, " s"),
            ("maximum", "Starting maximum", 1.0, 86400.0, " s"),
            ("increase_percent", "Increase after Yes", 0.01, 100.0, " %"),
            ("cap", "Maximum wait", 1.0, 86400.0, " s"),
        ):
            control = QDoubleSpinBox()
            control.setRange(minimum, maximum)
            control.setDecimals(2)
            control.setSuffix(suffix)
            control.setMinimumWidth(150)
            control.setMinimumHeight(34)
            control.setValue(getattr(self.settings, key))
            self.inputs[key] = control
            form.addRow(label, control)
        layout.addLayout(form)

        settings_actions = QHBoxLayout()
        self.apply_button = QPushButton("Apply and reset")
        self.apply_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.discard_button = QPushButton("Discard changes")
        settings_actions.addWidget(self.apply_button, 1)
        settings_actions.addWidget(self.discard_button, 1)
        layout.addLayout(settings_actions)
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)
        footer = QHBoxLayout()
        footer.addStretch()
        quit_button = QPushButton("Quit")
        quit_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton))
        footer.addWidget(quit_button)
        layout.addLayout(footer)

        self.start_button.clicked.connect(self.start_requested.emit)
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.reset_button.clicked.connect(self.reset_requested.emit)
        quit_button.clicked.connect(self.quit_requested.emit)
        self.apply_button.clicked.connect(self.submit_settings)
        self.discard_button.clicked.connect(self.discard_changes)
        for control in self.inputs.values():
            control.valueChanged.connect(self.update_dirty)
        self.update_dirty()

    def update_dirty(self) -> None:
        self.dirty = any(control.value() != getattr(self.settings, key) for key, control in self.inputs.items())
        self.apply_button.setEnabled(self.paused and self.dirty)
        self.discard_button.setEnabled(self.paused and self.dirty)
        self.start_button.setEnabled(self.paused and not self.dirty)

    def set_settings(self, settings: TimerSettings) -> None:
        self.settings = settings
        for key, control in self.inputs.items():
            control.setValue(getattr(settings, key))
        self.update_dirty()

    def discard_changes(self) -> None:
        self.set_settings(self.settings)
        self.set_notice("")

    def set_notice(self, message: str) -> None:
        self.notice.setText(message)
        self.notice.setVisible(bool(message))
        self.adjustSize()

    def submit_settings(self) -> None:
        try:
            settings = TimerSettings(**{key: control.value() for key, control in self.inputs.items()})
        except ValueError as error:
            self.set_notice(str(error))
            return
        self.settings_requested.emit(settings)

    def refresh(self, session: TrainingSession) -> None:
        self.paused = session.state is SessionState.PAUSED
        self.status_label.setText({
            SessionState.PAUSED: "Paused",
            SessionState.RUNNING: "Timer running",
            SessionState.AWAITING_ANSWER: "Waiting for your answer",
        }[session.state])
        self.range_label.setText(f"Current range: {range_text(session.lower, session.upper)}")
        for control in self.inputs.values():
            control.setEnabled(self.paused)
        self.pause_button.setEnabled(not self.paused)
        self.update_dirty()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.discard_changes()
        if not self.can_hide:
            self.quit_requested.emit()
        super().closeEvent(event)


class TimerController(QObject):
    def __init__(
        self,
        app: QApplication,
        storage: QSettings | None = None,
        session: TrainingSession | None = None,
        tray_available: bool | None = None,
    ) -> None:
        super().__init__(app)
        self.app = app
        self.storage = storage if storage is not None else QSettings("PuppyTimer", "PuppyTimer")
        self.storage.setFallbacksEnabled(False)
        settings, warning = load_settings(self.storage)
        self.session = session if session is not None else TrainingSession(settings)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.on_timeout)

        self.reminder = ReminderWindow()
        self.reminder.answered.connect(self.answer)
        self.reminder.dismissed.connect(self.pause)
        self.controls = SettingsDialog()
        self.controls.set_settings(self.session.settings)
        self.controls.set_notice(warning)
        self.controls.start_requested.connect(self.start_or_resume)
        self.controls.pause_requested.connect(self.pause)
        self.controls.reset_requested.connect(self.reset)
        self.controls.quit_requested.connect(self.quit)
        self.controls.settings_requested.connect(self.apply_settings)
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable() if tray_available is None else tray_available
        self.controls.can_hide = self.tray_available

        self.tray = QSystemTrayIcon(self)
        self.menu = QMenu(self.controls)
        self.status_action = self.menu.addAction("Paused")
        self.status_action.setEnabled(False)
        self.range_action = self.menu.addAction("")
        self.range_action.setEnabled(False)
        self.menu.addSeparator()
        self.start_action = self.add_action("Start / Resume", self.start_or_resume, QStyle.StandardPixmap.SP_MediaPlay)
        self.pause_action = self.add_action("Pause", self.pause, QStyle.StandardPixmap.SP_MediaPause)
        self.add_action("Reset", self.reset, QStyle.StandardPixmap.SP_BrowserReload)
        self.menu.addSeparator()
        self.add_action("Settings and controls...", self.show_controls, QStyle.StandardPixmap.SP_FileDialogDetailedView)
        self.add_action("Quit", self.quit, QStyle.StandardPixmap.SP_DialogCloseButton)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.menu.aboutToShow.connect(self.refresh)
        self.refresh()
        if self.tray_available:
            self.tray.show()
        self.app.primaryScreenChanged.connect(self.reminder.reposition)
        self.app.screenAdded.connect(self.watch_screen)
        for screen in self.app.screens():
            self.watch_screen(screen)
        self.app.aboutToQuit.connect(self.shutdown)

    def add_action(self, text: str, callback: Callable[[], None], icon: QStyle.StandardPixmap) -> QAction:
        action = self.menu.addAction(self.controls.style().standardIcon(icon), text)
        action.triggered.connect(callback)
        return action

    def watch_screen(self, screen: QScreen) -> None:
        screen.availableGeometryChanged.connect(self.reminder.reposition)

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_controls()

    def show_controls(self) -> None:
        self.refresh()
        self.controls.adjustSize()
        self.controls.showNormal()
        self.controls.raise_()
        self.controls.activateWindow()

    def refresh(self) -> None:
        self.controls.refresh(self.session)
        state = self.session.state
        status = {
            SessionState.PAUSED: "Paused",
            SessionState.RUNNING: "Timer running",
            SessionState.AWAITING_ANSWER: "Waiting for your answer",
        }[state]
        icon = {
            SessionState.PAUSED: QStyle.StandardPixmap.SP_MediaPause,
            SessionState.RUNNING: QStyle.StandardPixmap.SP_MediaPlay,
            SessionState.AWAITING_ANSWER: QStyle.StandardPixmap.SP_MessageBoxQuestion,
        }[state]
        self.tray.setIcon(self.controls.style().standardIcon(icon))
        self.tray.setToolTip(f"Puppy Timer: {status}\n{range_text(self.session.lower, self.session.upper)}")
        self.status_action.setText(status)
        self.range_action.setText(range_text(self.session.lower, self.session.upper))
        self.start_action.setEnabled(state is SessionState.PAUSED and not self.controls.dirty)
        self.pause_action.setEnabled(state is not SessionState.PAUSED)

    def arm_timer(self) -> None:
        self.timer.start(max(1, math.ceil(self.session.remaining_seconds * 1000)))

    def start_or_resume(self) -> None:
        if self.controls.dirty:
            self.controls.set_notice("Apply or discard your pending settings first.")
            self.show_controls()
            return
        if self.session.start_or_resume():
            self.arm_timer()
            if self.tray_available:
                self.controls.hide()
            self.refresh()

    def on_timeout(self) -> None:
        if self.session.expire():
            self.timer.stop()
            self.reminder.show_prompt(self.session.lower, self.session.upper)
            self.refresh()
        elif self.session.state is SessionState.RUNNING:
            self.arm_timer()

    def answer(self, settled: bool) -> None:
        if not self.session.answer(settled):
            return
        self.reminder.hide()
        if self.session.state is SessionState.RUNNING:
            self.arm_timer()
        else:
            self.timer.stop()
        self.refresh()

    def pause(self) -> None:
        self.timer.stop()
        self.session.pause()
        self.reminder.hide()
        self.refresh()

    def reset(self) -> None:
        self.timer.stop()
        self.session.reset()
        self.reminder.hide()
        self.controls.discard_changes()
        self.refresh()

    def apply_settings(self, settings: TimerSettings) -> None:
        if self.session.state is not SessionState.PAUSED:
            return
        if not save_settings(self.storage, settings):
            self.controls.set_notice("Could not save settings. The current session is unchanged.")
            return
        self.timer.stop()
        self.session.reset(settings)
        self.controls.set_settings(settings)
        self.controls.set_notice("")
        self.refresh()

    def shutdown(self) -> None:
        self.timer.stop()
        self.session.pause()
        self.reminder.hide()
        self.controls.hide()
        self.tray.hide()

    def quit(self) -> None:
        self.shutdown()
        self.app.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Puppy Timer")
    app.setOrganizationName("PuppyTimer")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setFont(QFont("Bahnschrift", 10))
    app.setStyleSheet(APP_STYLE)
    app.setWindowIcon(app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
    controller = TimerController(app)
    controller.show_controls()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())