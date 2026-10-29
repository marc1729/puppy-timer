# Puppy Timer

A small, silent Windows tray app for rewarding your puppy while she stays settled.
Built with Python and PySide6. No network connection, account, or server is needed
after installing dependencies.

## Run

Verified with Python 3.12 and PySide6 6.12.0 on Windows. From this folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\main.py
```

The environment and dependencies are already installed in this workspace. To launch
without a console window:

```powershell
.\.venv\Scripts\pythonw.exe .\main.py
```

For editing/debugging in VS Code, select the interpreter at
`.venv\Scripts\python.exe`. Launch one instance at a time.

## Use

1. Start the app. It opens paused with the settings/control window visible.
2. Select **Start / Resume**. The window hides and a random countdown begins.
3. When the small reminder appears, check whether she is still settled.
4. **Yes:** give the treat, then click Yes. The interval range grows and the next
   countdown starts from your click.
5. **No:** the prompt hides and the timer pauses. The current range is unchanged.
6. When she has settled again, right-click the tray icon beside the clock and choose
   **Start / Resume**. This draws a new wait from the same range, not the starting range.

Windows may place the tray icon in the hidden-icons menu. Double-click it to open
the controls. Its tooltip shows the current state and interval range.

- An unanswered prompt remains visible. It never schedules repeated reminders or
  assumes a successful reward.
- **Pause** cancels the current wait without changing the range. Resume always starts
  a fresh full wait.
- **Reset** restores the configured starting range and remains paused.
- **Quit** exits completely. Closing the controls normally hides them to the tray.
- If the system tray is unavailable, the controls stay visible while the timer runs;
  closing that fallback window exits the app.

## Intervals and Settings

Defaults are an **8-12 second** starting range, **5%** increase after every Yes,
and a **120 second** maximum wait. Each duration is sampled uniformly within the
current range. The first Yes changes 8-12 to 8.4-12.6 seconds.

Both bounds grow together until the upper bound reaches the cap. The default final
range is **80-120 seconds**, preserving variation instead of eventually becoming a
fixed two-minute timer. Individual waits can get shorter even as the range grows.

Settings are editable only while paused. **Apply and reset** saves the preferences,
restores the new starting range, and stays paused. **Discard changes** leaves the
session untouched. Pending edits must be applied or discarded before starting.

Settings require `1 <= minimum < maximum <= cap <= 86400` seconds and an increase
greater than 0% and no more than 100%. Values in the controls use two decimal places.
The interval model retains full precision; the displayed current range is rounded.

Preferences are stored for the current Windows user using Qt's `QSettings`, under:

```text
HKEY_CURRENT_USER\Software\PuppyTimer\PuppyTimer
```

Session progress and active timers are not saved. Relaunching always starts paused
at the saved starting range. Invalid saved settings fall back to defaults with a
visible warning; failed saves leave the current session unchanged.

## Windows Behavior

The reminder is an application-owned, always-on-top tool window, not a Windows
notification toast. Do Not Disturb's notification filtering is therefore not its
delivery path. The app does not change Windows notification settings.

It appears near the bottom-right of the primary display's usable area without
activating itself or grabbing keyboard focus. Intentional clicks can activate it.
The prompt is silent and remains until answered or paused from the tray.

Limitations:

- No visibility over the lock screen or UAC secure desktop. Exclusive-fullscreen
  applications and other topmost windows can obscure it.
- The computer must be awake to deliver reminders. A late timeout produces at most
  one pending prompt, not a backlog. There is no automatic wake or catch-up progress.
- Timing during sleep is not guaranteed. Pause before stepping away, then Resume
  when ready.
- No global keyboard shortcuts, launch-at-login, training history, or executable
  packaging in this first version.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover interval bounds/growth/caps, state transitions, duplicate answers,
stale callbacks, actual Qt timer expiry, real Yes/No button clicks, tray lifecycle,
settings validation and persistence, failure handling, and layout bounds.
They use temporary preferences rather than your saved settings. Some tests briefly
show windows. Native Windows tests verify the topmost style and unchanged foreground
window when the reminder appears.

Manual acceptance checks (not replaced by unit tests):

1. Enable Windows Do Not Disturb, start a session, and type in VS Code or a browser.
   Confirm the reminder appears without interrupting typing. Repeat with DND off.
2. Choose Yes and confirm another reminder follows. Choose No and confirm no more
   reminders appear until you Resume from the tray.
3. Check display scaling, primary-monitor changes and taskbar placement. The question,
   settings labels and button text should stay readable and entirely on screen.
4. Sleep/lock and return during a running session. There must be no burst of reminders
   or automatic increase; any pending prompt still requires an answer.
5. Quit and relaunch. Preferences should remain, while the session is paused at its
   starting range.