from __future__ import annotations

import ctypes
import os
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStyle, QStyleOptionSpinBox, QSystemTrayIcon

from main import (
    APP_STYLE, ReminderWindow, SessionState, TimerController, TimerSettings,
    TrainingSession, load_settings, save_settings,
)


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Mock(return_value=100.0)
        self.session = TrainingSession(rng=random.Random(42), clock=self.clock)

    def expire_next(self) -> None:
        self.assertIsNotNone(self.session.deadline)
        self.clock.return_value = self.session.deadline
        self.assertTrue(self.session.expire())

    def test_initial_state_and_sample(self) -> None:
        self.assertEqual(self.session.state, SessionState.PAUSED)
        self.assertTrue(self.session.start_or_resume())
        self.assertGreaterEqual(self.session.remaining_seconds, 8)
        self.assertLessEqual(self.session.remaining_seconds, 12)
        deadline = self.session.deadline
        self.assertFalse(self.session.start_or_resume())
        self.assertEqual(self.session.deadline, deadline)

    def test_yes_increases_both_bounds_and_restarts(self) -> None:
        self.session.start_or_resume()
        self.expire_next()
        self.assertTrue(self.session.answer(True))
        self.assertAlmostEqual(self.session.lower, 8.4)
        self.assertAlmostEqual(self.session.upper, 12.6)
        self.assertEqual(self.session.state, SessionState.RUNNING)
        self.assertGreaterEqual(self.session.remaining_seconds, 8.4)
        self.assertLessEqual(self.session.remaining_seconds, 12.6)

    def test_no_preserves_progress_and_resume_uses_same_range(self) -> None:
        self.session.start_or_resume()
        self.expire_next()
        self.session.answer(True)
        self.expire_next()
        bounds = self.session.lower, self.session.upper
        self.assertTrue(self.session.answer(False))
        self.assertEqual(self.session.state, SessionState.PAUSED)
        self.assertIsNone(self.session.deadline)
        self.assertEqual((self.session.lower, self.session.upper), bounds)
        self.clock.return_value += 1000
        self.assertFalse(self.session.expire())
        self.session.start_or_resume()
        self.assertEqual((self.session.lower, self.session.upper), bounds)
        self.assertGreaterEqual(self.session.remaining_seconds, bounds[0])
        self.assertLessEqual(self.session.remaining_seconds, bounds[1])

    def test_cap_preserves_random_range(self) -> None:
        self.session.start_or_resume()
        for _ in range(200):
            self.expire_next()
            self.session.answer(True)
            self.assertLessEqual(self.session.upper, 120)
            self.assertAlmostEqual(self.session.lower / self.session.upper, 2 / 3)
            self.assertGreaterEqual(self.session.remaining_seconds + 1e-10, self.session.lower)
            self.assertLessEqual(self.session.remaining_seconds - 1e-10, self.session.upper)
        self.assertAlmostEqual(self.session.lower, 80)
        self.assertEqual(self.session.upper, 120)

    def test_cap_applies_partial_growth(self) -> None:
        self.session = TrainingSession(TimerSettings(8, 12, 5, 12.3), clock=self.clock)
        self.session.start_or_resume()
        self.expire_next()
        self.session.answer(True)
        self.assertAlmostEqual(self.session.lower, 8.2)
        self.assertEqual(self.session.upper, 12.3)

    def test_no_answer_means_no_progress_or_more_expiries(self) -> None:
        self.session.start_or_resume()
        self.expire_next()
        self.clock.return_value += 10000
        self.assertFalse(self.session.expire())
        self.assertFalse(self.session.start_or_resume())
        self.assertEqual(self.session.state, SessionState.AWAITING_ANSWER)
        self.assertEqual((self.session.lower, self.session.upper), (8, 12))

    def test_duplicate_answers_are_ignored(self) -> None:
        self.assertFalse(self.session.answer(True))
        self.session.start_or_resume()
        self.assertFalse(self.session.answer(False))
        self.expire_next()
        self.session.answer(True)
        bounds = self.session.lower, self.session.upper
        deadline = self.session.deadline
        self.assertFalse(self.session.answer(True))
        self.assertFalse(self.session.answer(False))
        self.assertEqual((self.session.lower, self.session.upper), bounds)
        self.assertEqual(self.session.deadline, deadline)

    def test_pause_resume_rejects_old_timeout(self) -> None:
        self.session.start_or_resume()
        self.clock.return_value = self.session.deadline - 0.5
        self.session.pause()
        self.session.start_or_resume()
        self.clock.return_value += 0.5
        self.assertFalse(self.session.expire())
        self.assertEqual(self.session.state, SessionState.RUNNING)

    def test_reset_restores_initial_range_and_pauses(self) -> None:
        self.session.start_or_resume()
        self.expire_next()
        self.session.answer(True)
        self.session.reset()
        self.assertEqual(self.session.state, SessionState.PAUSED)
        self.assertEqual((self.session.lower, self.session.upper), (8, 12))
        self.assertIsNone(self.session.deadline)
        settings = TimerSettings(3, 5, 10, 30)
        self.session.reset(settings)
        self.assertEqual(self.session.settings, settings)
        self.assertEqual((self.session.lower, self.session.upper), (3, 5))

    def test_invalid_settings(self) -> None:
        for changes in (
            {"minimum": 0}, {"minimum": 12}, {"maximum": 121}, {"cap": 11},
            {"cap": 86401}, {"increase_percent": 0}, {"increase_percent": 101},
            {"minimum": float("nan")}, {"maximum": float("inf")},
            {"increase_percent": float("nan")}, {"cap": float("inf")},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                TimerSettings(**changes)


class ReminderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")
        cls.app.setFont(QFont("Bahnschrift", 10))
        cls.app.setStyleSheet(APP_STYLE)

    def setUp(self) -> None:
        self.reminder = ReminderWindow()

    def tearDown(self) -> None:
        self.reminder.hide()
        self.reminder.deleteLater()
        self.app.processEvents()

    def test_flags_and_screen_position(self) -> None:
        self.reminder.show_prompt(8, 12)
        self.app.processEvents()
        self.assertTrue(self.reminder.isVisible())
        self.assertTrue(self.reminder.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.reminder.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        screen = QGuiApplication.primaryScreen()
        self.assertIsNotNone(screen)
        self.assertTrue(screen.availableGeometry().contains(self.reminder.frameGeometry()))

    def test_buttons_emit_answers(self) -> None:
        answers = QSignalSpy(self.reminder.answered)
        self.reminder.show_prompt(8, 12)
        QTest.mouseClick(self.reminder.yes_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.reminder.no_button, Qt.MouseButton.LeftButton)
        self.assertEqual(answers.count(), 2)
        self.assertEqual(answers.at(0), [True])
        self.assertEqual(answers.at(1), [False])

    def test_close_emits_dismissed(self) -> None:
        dismissed = QSignalSpy(self.reminder.dismissed)
        self.reminder.show_prompt(8, 12)
        self.reminder.close()
        self.assertEqual(dismissed.count(), 1)

    @unittest.skipUnless(sys.platform == "win32" and os.getenv("QT_QPA_PLATFORM") != "offscreen", "Native Windows check")
    def test_native_topmost_without_focus_theft(self) -> None:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        before = user32.GetForegroundWindow()
        self.reminder.show_prompt(8, 12)
        self.app.processEvents()
        QTest.qWait(80)
        self.assertEqual(user32.GetForegroundWindow(), before)
        extended_style = user32.GetWindowLongW(int(self.reminder.winId()), -20)
        self.assertTrue(extended_style & 0x00000008, "WS_EX_TOPMOST must be set")


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")
        cls.app.setFont(QFont("Bahnschrift", 10))
        cls.app.setStyleSheet(APP_STYLE)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.storage = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.clock = Mock(return_value=100.0)
        session = TrainingSession(rng=random.Random(42), clock=self.clock)
        self.controller = TimerController(self.app, self.storage, session, tray_available=False)

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.controller.reminder.deleteLater()
        self.controller.controls.deleteLater()
        self.controller.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def expire_next(self) -> None:
        self.clock.return_value = self.controller.session.deadline
        self.controller.timer.timeout.emit()

    def test_yes_no_resume_flow_using_real_buttons(self) -> None:
        self.controller.controls.start_button.click()
        self.assertTrue(self.controller.timer.isActive())
        self.expire_next()
        self.assertTrue(self.controller.reminder.isVisible())
        self.assertFalse(self.controller.timer.isActive())
        QTest.mouseClick(self.controller.reminder.yes_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.controller.reminder.isVisible())
        self.assertTrue(self.controller.timer.isActive())
        self.assertAlmostEqual(self.controller.session.upper, 12.6)
        self.expire_next()
        bounds = self.controller.session.lower, self.controller.session.upper
        QTest.mouseClick(self.controller.reminder.no_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.controller.session.state, SessionState.PAUSED)
        self.assertFalse(self.controller.timer.isActive())
        self.assertFalse(self.controller.reminder.isVisible())
        self.assertEqual((self.controller.session.lower, self.controller.session.upper), bounds)
        self.controller.start_action.trigger()
        self.assertTrue(self.controller.timer.isActive())
        self.assertEqual((self.controller.session.lower, self.controller.session.upper), bounds)

    def test_early_callback_does_not_show_reminder(self) -> None:
        self.controller.start_or_resume()
        self.controller.timer.timeout.emit()
        self.assertFalse(self.controller.reminder.isVisible())
        self.assertTrue(self.controller.timer.isActive())

    def test_real_timer_expiry(self) -> None:
        self.controller.session = TrainingSession(TimerSettings(1, 1.01, 5, 120))
        self.controller.controls.set_settings(self.controller.session.settings)
        expired = QSignalSpy(self.controller.timer.timeout)
        self.controller.start_or_resume()
        deadline = time.monotonic() + 2.5
        while not self.controller.reminder.isVisible() and time.monotonic() < deadline:
            expired.wait(max(1, int((deadline - time.monotonic()) * 1000)))
        self.assertGreaterEqual(expired.count(), 1)
        self.assertTrue(self.controller.reminder.isVisible())
        self.assertFalse(self.controller.timer.isActive())
        self.assertEqual(self.controller.session.state, SessionState.AWAITING_ANSWER)

    def test_close_reminder_pauses_without_growing(self) -> None:
        self.controller.start_or_resume()
        self.expire_next()
        self.controller.reminder.close()
        self.assertEqual(self.controller.session.state, SessionState.PAUSED)
        self.assertEqual(self.controller.session.upper, 12)
        self.assertFalse(self.controller.timer.isActive())

    def test_stale_timeout_and_duplicate_click_are_ignored(self) -> None:
        self.controller.start_or_resume()
        self.expire_next()
        self.controller.answer(True)
        deadline = self.controller.session.deadline
        self.controller.answer(True)
        self.assertEqual(self.controller.session.deadline, deadline)
        self.controller.pause()
        self.clock.return_value += 1000
        self.controller.timer.timeout.emit()
        self.assertFalse(self.controller.reminder.isVisible())
        self.assertFalse(self.controller.timer.isActive())

    def test_settings_validate_save_and_reset(self) -> None:
        controls = self.controller.controls
        controls.inputs["minimum"].setValue(20)
        controls.apply_button.click()
        self.assertIn("minimum < maximum", controls.notice.text())
        self.assertEqual(self.controller.session.settings, TimerSettings())
        controls.inputs["maximum"].setValue(30)
        controls.apply_button.click()
        self.assertEqual(self.controller.session.settings, TimerSettings(20, 30, 5, 120))
        self.assertEqual(self.controller.session.state, SessionState.PAUSED)
        self.assertFalse(self.controller.timer.isActive())
        reloaded = QSettings(self.storage.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(load_settings(reloaded), (TimerSettings(20, 30, 5, 120), ""))

    def test_unsaved_settings_block_start_and_discard_is_nonmutating(self) -> None:
        controls = self.controller.controls
        controls.inputs["minimum"].setValue(7)
        self.assertFalse(controls.start_button.isEnabled())
        self.controller.start_or_resume()
        self.assertFalse(self.controller.timer.isActive())
        controls.discard_button.click()
        self.assertEqual(controls.inputs["minimum"].value(), 8)
        self.assertEqual(self.controller.session.settings, TimerSettings())
        self.assertTrue(controls.start_button.isEnabled())

    def test_typed_setting_can_be_applied_without_extra_enter(self) -> None:
        controls = self.controller.controls
        controls.show()
        field = controls.inputs["minimum"]
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(field, "9")
        self.assertTrue(controls.apply_button.isEnabled())
        QTest.mouseClick(controls.apply_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.controller.session.settings.minimum, 9)
        self.assertFalse(controls.dirty)

    def test_settings_disabled_during_session(self) -> None:
        self.controller.start_or_resume()
        self.assertFalse(self.controller.controls.inputs["minimum"].isEnabled())
        self.controller.apply_settings(TimerSettings(3, 5, 10, 30))
        self.assertEqual(self.controller.session.settings, TimerSettings())
        self.controller.pause()
        self.assertTrue(self.controller.controls.inputs["minimum"].isEnabled())

    def test_corrupt_saved_preferences_use_defaults(self) -> None:
        self.storage.setValue("minimum", "not a number")
        settings, warning = load_settings(self.storage)
        self.assertEqual(settings, TimerSettings())
        self.assertTrue(warning)

    def test_relaunch_restores_preferences_but_not_progress(self) -> None:
        settings = TimerSettings(3, 5, 10, 30)
        self.assertTrue(save_settings(self.storage, settings))
        reloaded = QSettings(self.storage.fileName(), QSettings.Format.IniFormat)
        fresh = TimerController(self.app, reloaded, tray_available=False)
        try:
            self.assertEqual(fresh.session.settings, settings)
            self.assertEqual((fresh.session.lower, fresh.session.upper), (3, 5))
            self.assertEqual(fresh.session.state, SessionState.PAUSED)
            self.assertFalse(fresh.timer.isActive())
        finally:
            fresh.shutdown()
            fresh.reminder.deleteLater()
            fresh.controls.deleteLater()
            fresh.deleteLater()

    def test_failed_save_does_not_change_session(self) -> None:
        storage = Mock(spec=QSettings)
        storage.status.return_value = QSettings.Status.AccessError
        self.controller.storage = storage
        self.controller.apply_settings(TimerSettings(3, 5, 10, 30))
        self.assertEqual(self.controller.session.settings, TimerSettings())
        self.assertIn("Could not save", self.controller.controls.notice.text())

    def test_without_tray_controls_stay_visible(self) -> None:
        self.controller.show_controls()
        self.controller.start_or_resume()
        self.assertTrue(self.controller.controls.isVisible())
        self.assertFalse(self.controller.controls.can_hide)

    def test_tray_keeps_app_accessible_with_hidden_windows(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.skipTest("No native system tray available")
        self.controller.tray_available = True
        self.controller.controls.can_hide = True
        self.controller.tray.show()
        self.controller.controls.show()
        self.controller.start_or_resume()
        self.assertFalse(self.controller.controls.isVisible())
        self.assertFalse(self.controller.reminder.isVisible())
        self.assertTrue(self.controller.tray.isVisible())
        self.assertTrue(self.controller.timer.isActive())
        self.assertFalse(self.app.quitOnLastWindowClosed())
        self.assertIsNotNone(self.controller.tray.contextMenu())

    def test_controls_fit_content(self) -> None:
        self.controller.controls.show()
        self.controller.start_or_resume()
        self.expire_next()
        self.app.processEvents()
        for button in self.controller.controls.findChildren(QPushButton):
            with self.subTest(button=button.text() or button.accessibleName()):
                self.assertGreaterEqual(button.width(), button.sizeHint().width())
        for label in self.controller.controls.findChildren(QLabel):
            if label.isVisible() and not label.wordWrap():
                with self.subTest(label=label.text()):
                    self.assertGreaterEqual(label.contentsRect().width(), label.fontMetrics().horizontalAdvance(label.text()))

    def test_numeric_stepper_arrows_are_visible_and_clickable(self) -> None:
        self.controller.controls.show()
        self.app.processEvents()
        field = self.controller.controls.inputs["minimum"]
        option = QStyleOptionSpinBox()
        field.initStyleOption(option)
        image = field.grab().toImage()
        scale = image.devicePixelRatio()
        for subcontrol in (QStyle.SubControl.SC_SpinBoxUp, QStyle.SubControl.SC_SpinBoxDown):
            area = field.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, subcontrol, field)
            inside = area.adjusted(3, 3, -3, -3)
            dark_pixels = sum(
                image.pixelColor(horizontal, vertical).lightness() < 100
                for horizontal in range(int(inside.left() * scale), int(inside.right() * scale))
                for vertical in range(int(inside.top() * scale), int(inside.bottom() * scale))
            )
            self.assertGreater(dark_pixels, 0, f"Missing stepper arrow: {subcontrol}")
            before = field.value()
            QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=area.center())
            expected = before + (1 if subcontrol == QStyle.SubControl.SC_SpinBoxUp else -1)
            self.assertEqual(field.value(), expected)

    def test_shutdown_stops_everything(self) -> None:
        self.controller.start_or_resume()
        self.controller.shutdown()
        self.assertFalse(self.controller.timer.isActive())
        self.assertFalse(self.controller.reminder.isVisible())
        self.assertFalse(self.controller.controls.isVisible())
        self.assertFalse(self.controller.tray.isVisible())


if __name__ == "__main__":
    unittest.main()