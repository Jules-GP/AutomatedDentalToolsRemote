"""Single source of truth for colors, spacing and styled-widget factories.

No module built on ServerToolsCore should write its own CSS. Changing the
primary color, or any other token, is a one-file edit that propagates to every
module using these factories. `_isDarkMode` (here: is_dark_mode) exists in
exactly one place in the whole extension.
"""

import qt
import slicer

SPACING_XS = 4
SPACING_SM = 6
SPACING_MD = 8
SPACING_LG = 12

_LIGHT = {
    "PRIMARY": "#3498db",
    "PRIMARY_HOVER": "#2980b9",
    "PRIMARY_PRESSED": "#1f618d",
    "DANGER": "#e74c3c",
    "DANGER_HOVER": "#c0392b",
    "DANGER_PRESSED": "#922b21",
    "SUCCESS": "#27ae60",
    "TEXT": "#2c3e50",
    "TEXT_MUTED": "#34495e",
    "BORDER": "#e0e6ed",
    "BACKGROUND": "#f8f9fa",
    "SURFACE": "#ffffff",
    "SURFACE_HOVER": "#fbfcfd",
    "DISABLED_BG": "#bdc3c7",
    "DISABLED_TEXT": "#95a5a6",
}

_DARK = {
    "PRIMARY": "#4ba3ff",
    "PRIMARY_HOVER": "#3498db",
    "PRIMARY_PRESSED": "#2980b9",
    "DANGER": "#e74c3c",
    "DANGER_HOVER": "#ec7063",
    "DANGER_PRESSED": "#a93226",
    "SUCCESS": "#2ecc71",
    "TEXT": "#e0e0e0",
    "TEXT_MUTED": "#b0b0b0",
    "BORDER": "#454545",
    "BACKGROUND": "#2b2b2b",
    "SURFACE": "#383838",
    "SURFACE_HOVER": "#414141",
    "DISABLED_BG": "#555555",
    "DISABLED_TEXT": "#888888",
}

# (top, bottom) gradient stops per button role and state. The vertical
# qlineargradient is the SlicerAutomatedDentalTools button: every .ui of the
# original extension paints QPushButton with exactly it, and the flat fill
# that shipped here first read as a different product next to those modules.
# Dark accents follow the original's applyDarkModeStyles (#5dade2 family).
_BUTTON_STOPS_LIGHT = {
    "primary":   {"base": ("#4ba3ff", "#3498db"), "hover": ("#5cb3ff", "#2980b9"), "pressed": ("#2980b9", "#1f618d")},
    "danger":    {"base": ("#ec7063", "#e74c3c"), "hover": ("#f1948a", "#c0392b"), "pressed": ("#c0392b", "#922b21")},
    "success":   {"base": ("#66bb6a", "#4caf50"), "hover": ("#81c784", "#43a047"), "pressed": ("#43a047", "#2e7d32")},
    "secondary": {"base": ("#78909c", "#607d8b"), "hover": ("#90a4ae", "#546e7a"), "pressed": ("#546e7a", "#455a64")},
}
_BUTTON_STOPS_DARK = {
    "primary":   {"base": ("#5dade2", "#3498db"), "hover": ("#7bbcef", "#5dade2"), "pressed": ("#3498db", "#2980b9")},
    "danger":    {"base": ("#ec7063", "#e74c3c"), "hover": ("#f1948a", "#ec7063"), "pressed": ("#c0392b", "#a93226")},
    "success":   {"base": ("#58d68d", "#2ecc71"), "hover": ("#82e0aa", "#58d68d"), "pressed": ("#2ecc71", "#28b463")},
    "secondary": {"base": ("#90a4ae", "#78909c"), "hover": ("#b0bec5", "#90a4ae"), "pressed": ("#78909c", "#607d8b")},
}

# The two colors of a checkable on/off button (see toggle_button). Fixed
# Material values in both themes, exactly as GreedyReg's interactive-tool
# toggle: blue reads "click to start", red reads "active, click to stop".
_TOGGLE_OFF = "#2196f3"
_TOGGLE_ON = "#f44336"

# White check mark drawn inside a checked QCheckBox indicator. An inline SVG
# rather than a Qt resource (:/Icons/SmallCheckMark.png in the original .ui
# files) so it needs no resource file compiled into the extension.
_CHECKMARK_SVG = (
    "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
    "<path fill='white' d='M13.854 3.646a.5.5 0 0 1 0 .708l-7 7a.5.5 0 0 1-.708 0"
    "l-3.5-3.5a.5.5 0 1 1 .708-.708L6.5 10.293l6.646-6.647a.5.5 0 0 1 .708 0z'/></svg>"
)


def is_dark_mode() -> bool:
    try:
        palette = slicer.app.palette()
        bg = palette.color(qt.QPalette.Window)
        luminance = (0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()) / 255.0
        return luminance < 0.5
    except Exception:
        return False


def tokens() -> dict:
    """Resolved palette for the current theme. Always re-reads the app palette,
    so a mode switch takes effect the next time a factory or apply() runs."""
    return _DARK if is_dark_mode() else _LIGHT


def _base_stylesheet(t: dict) -> str:
    return f"""
    qMRMLWidget {{ background-color: {t['BACKGROUND']}; }}
    ctkCollapsibleButton {{
      background-color: {t['SURFACE']};
      border: 1px solid {t['BORDER']};
      border-radius: 6px;
      margin-bottom: {SPACING_MD}px;
      font-weight: 600;
      padding: {SPACING_SM}px 10px;
      color: {t['TEXT']};
    }}
    ctkCollapsibleButton:hover {{
      border: 1px solid {t['PRIMARY']};
      background-color: {t['SURFACE_HOVER']};
    }}
    QLabel {{
      color: {t['TEXT']};
      font-weight: 500;
    }}
    QLineEdit, QTextEdit {{
      background-color: {t['SURFACE']};
      border: 1px solid {t['BORDER']};
      border-radius: 4px;
      padding: {SPACING_SM}px;
      color: {t['TEXT']};
      selection-background-color: {t['PRIMARY']};
    }}
    QLineEdit:focus, QTextEdit:focus {{
      border: 2px solid {t['PRIMARY']};
    }}
    QComboBox {{
      background-color: {t['SURFACE']};
      border: 1px solid {t['BORDER']};
      border-radius: 4px;
      padding: {SPACING_XS}px {SPACING_SM}px;
      color: {t['TEXT']};
    }}
    QComboBox:focus {{ border: 2px solid {t['PRIMARY']}; }}
    QComboBox::drop-down {{ width: 20px; border: none; }}
    QComboBox QAbstractItemView {{
      background-color: {t['SURFACE']};
      color: {t['TEXT']};
      selection-background-color: {t['PRIMARY']};
      border: 1px solid {t['BORDER']};
    }}
    QSpinBox, QDoubleSpinBox {{
      background-color: {t['SURFACE']};
      border: 1px solid {t['BORDER']};
      border-radius: 4px;
      padding: {SPACING_XS}px {SPACING_SM}px;
      color: {t['TEXT']};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {t['PRIMARY']}; }}
    QCheckBox {{
      color: {t['TEXT']};
      font-weight: 500;
      spacing: {SPACING_SM}px;
    }}
    QCheckBox::indicator {{
      width: 18px;
      height: 18px;
      border: 1px solid {t['BORDER']};
      border-radius: 3px;
      background-color: {t['SURFACE']};
    }}
    QCheckBox::indicator:hover {{
      border: 1px solid {t['PRIMARY']};
      background-color: {t['SURFACE_HOVER']};
    }}
    QCheckBox::indicator:checked {{
      background-color: {t['PRIMARY']};
      border: 1px solid {t['PRIMARY']};
      image: url("{_CHECKMARK_SVG}");
    }}
    QSlider::groove:horizontal {{
      border: 1px solid {t['BORDER']};
      height: 8px;
      background-color: {t['SURFACE']};
      border-radius: 4px;
    }}
    QSlider::handle:horizontal {{
      background-color: {t['PRIMARY']};
      border: 1px solid {t['PRIMARY']};
      width: 16px;
      margin: -4px 0;
      border-radius: 8px;
    }}
    QSlider::handle:horizontal:hover {{
      background-color: {t['PRIMARY_HOVER']};
      border: 1px solid {t['PRIMARY_HOVER']};
    }}
    QProgressBar {{
      border: 1px solid {t['BORDER']};
      border-radius: 4px;
      background-color: {t['SURFACE']};
      padding: 2px;
      color: {t['TEXT']};
    }}
    QProgressBar::chunk {{
      background-color: {t['PRIMARY']};
      border-radius: 3px;
    }}
    {_button_stylesheet("primary", t)}
    """


def _gradient(top: str, bottom: str) -> str:
    return f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bottom})"


def _button_stops() -> dict:
    return _BUTTON_STOPS_DARK if is_dark_mode() else _BUTTON_STOPS_LIGHT


def _button_stylesheet(role: str, t: dict) -> str:
    """The QSS of one button role. Also embedded in the base stylesheet as the
    bare-QPushButton rule (role "primary"), so a plain button someone adds
    (formgen's File.../Folder... browse buttons) comes out looking like the
    original's Search buttons rather than falling back to Slicer's default."""
    stops = _button_stops()[role]
    return f"""
    QPushButton {{
      background-color: {_gradient(*stops['base'])};
      color: white;
      border: none;
      border-radius: 6px;
      font-weight: 600;
      font-size: 10pt;
      padding: {SPACING_MD}px;
      margin-top: {SPACING_XS}px;
    }}
    QPushButton:hover:!pressed {{ background-color: {_gradient(*stops['hover'])}; }}
    QPushButton:pressed {{ background-color: {_gradient(*stops['pressed'])}; }}
    QPushButton:disabled {{ background-color: {t['DISABLED_BG']}; color: {t['DISABLED_TEXT']}; }}
    """


def apply(widget) -> None:
    """Apply the current theme's stylesheet to a widget tree (e.g. the module's root widget)."""
    widget.setStyleSheet(_base_stylesheet(tokens()))


def _role_button(text: str, role: str) -> qt.QPushButton:
    button = qt.QPushButton(text)
    button.setStyleSheet(_button_stylesheet(role, tokens()))
    return button


def primary_button(text: str) -> qt.QPushButton:
    """The panel's main action: Apply, Retry."""
    return _role_button(text, "primary")


def danger_button(text: str) -> qt.QPushButton:
    """A destructive or interrupting action: Cancel."""
    return _role_button(text, "danger")


def success_button(text: str) -> qt.QPushButton:
    """A confirming action distinct from the main one: GreedyReg's green
    Run/Save family. Not used by the generated panel itself; offered to
    modules adding their own buttons (addExtraWidgets)."""
    return _role_button(text, "success")


def secondary_button(text: str) -> qt.QPushButton:
    """A secondary tool that must not compete with the main action: the
    blue-gray of the original's utility buttons."""
    return _role_button(text, "secondary")


def compact_button(text: str) -> qt.QPushButton:
    """A small inline button for a form row (the browse actions): the
    primary gradient with tighter padding and no top margin, so a row of them
    stays one text-field tall and the whole input fits on a single line."""
    t = tokens()
    stops = _button_stops()["primary"]
    button = qt.QPushButton(text)
    button.setStyleSheet(
        f"QPushButton {{ background-color: {_gradient(*stops['base'])}; color: white;"
        f" border: none; border-radius: 4px; font-weight: 600;"
        f" padding: {SPACING_XS}px {SPACING_MD}px; margin: 0px; }}"
        f"QPushButton:hover:!pressed {{ background-color: {_gradient(*stops['hover'])}; }}"
        f"QPushButton:pressed {{ background-color: {_gradient(*stops['pressed'])}; }}"
        f"QPushButton:disabled {{ background-color: {t['DISABLED_BG']}; color: {t['DISABLED_TEXT']}; }}"
    )
    return button


def toggle_button(text: str) -> qt.QPushButton:
    """A checkable on/off button: blue when off ("click to start"), red while
    checked ("active, click to stop"), as GreedyReg's interactive-tool toggle.
    Flat fills, not gradients: the two-state color IS the information, and a
    gradient would make it read as one more action button."""
    t = tokens()
    button = qt.QPushButton(text)
    button.setCheckable(True)
    button.setStyleSheet(
        f"QPushButton {{ background-color: {_TOGGLE_OFF}; color: white; border: none;"
        f" border-radius: 4px; font-weight: 600; padding: {SPACING_SM}px; }}"
        f"QPushButton:checked {{ background-color: {_TOGGLE_ON}; }}"
        f"QPushButton:disabled {{ background-color: {t['DISABLED_BG']}; color: {t['DISABLED_TEXT']}; }}"
    )
    return button


def section_title(text: str) -> qt.QLabel:
    t = tokens()
    label = qt.QLabel(text)
    label.setStyleSheet(f"color: {t['TEXT_MUTED']}; font-weight: 600;")
    return label


def required_label(text: str) -> qt.QLabel:
    return section_title(f"{text} *")


def optional_label(text: str) -> qt.QLabel:
    """A file argument the tool can do without.

    Said in words rather than by the absence of the `*`: an empty file picker
    looks like a demand whatever the label does, and a tool that computes the
    file itself when it is left empty -- AREG's landmarks, produced by ALI
    through the supervisor -- otherwise reads as a missing input.
    """
    return section_title(f"{text} (optional)")


def hint_label(text: str) -> qt.QLabel:
    """A wrapped, muted, smaller label for explanatory text shown next to a
    field — the server's own `description` when a tooltip is not enough (see
    formgen.MultiChoiceGroup)."""
    t = tokens()
    label = qt.QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {t['TEXT_MUTED']}; font-size: 8pt; padding-bottom: {SPACING_XS}px;")
    return label


def link_button(text: str) -> qt.QPushButton:
    """A small, flat, text-only button for a secondary action next to a field —
    the All / None / Default row above a group of check boxes.

    Deliberately NOT primary_button: three filled blue buttons above a check
    box grid read as the panel's main actions and compete with Apply, which is
    the one button that starts a run.
    """
    t = tokens()
    button = qt.QPushButton(text)
    button.setStyleSheet(
        f"QPushButton {{ background: transparent; border: none; color: {t['PRIMARY']};"
        f" font-size: 8pt; font-weight: 600; padding: 0px {SPACING_SM}px; margin: 0px;"
        f" text-decoration: underline; }}"
        f"QPushButton:hover {{ color: {t['PRIMARY_HOVER']}; }}"
    )
    # A QCursor, not the bare Qt::CursorShape enum: PyQt converts one to the
    # other implicitly, PythonQt does not reliably, and this runs under
    # PythonQt.
    button.setCursor(qt.QCursor(qt.Qt.PointingHandCursor))
    return button


# A QScrollArea's size hint ignores its child, so a chart or a tab page laid
# out inside one collapses to a few pixels unless it is told how tall it is.
# Both are floors, not fixed heights: the layouts still grow with the panel.
CHART_MIN_HEIGHT = 90   # two rows of check boxes plus their group labels
TABS_MIN_HEIGHT = 220   # a tab bar plus roughly six rows of options

# Joystick pad (joystick.JoystickPad). The side is FlexReg's PAD_SIZE; the
# paint colors are FlexReg's pad palette, which was designed against this same
# blue theme. Hex strings rather than QColors so this module stays importable
# under the test stubs; the pad wraps them at paint time.
PAD_SIZE = 160
_PAD_LIGHT = {
    "background": "#f4f7fa", "border": "#d3dce5", "grid": "#e3eaf1",
    "text": "#93a2b1", "label": "#6b7c8d", "knob": "#3498db", "trail": "#bcd7ef",
}
_PAD_DARK = {
    "background": "#2b3138", "border": "#4a5560", "grid": "#3d454e",
    "text": "#8b97a3", "label": "#b6c2ce", "knob": "#4ba3ff", "trail": "#3f5871",
}


def pad_palette() -> dict:
    """The joystick pad's paint colors for the current theme, as hex strings."""
    return _PAD_DARK if is_dark_mode() else _PAD_LIGHT


def warning_label(text: str) -> qt.QLabel:
    """A visible, wrapped, danger-colored label — used when part of a module's
    UI could not be built, so a failure is never just a silent blank panel."""
    t = tokens()
    label = qt.QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {t['DANGER']}; font-weight: 600; padding: {SPACING_SM}px;")
    return label


def status_badge() -> qt.QLabel:
    """An initial, unresolved badge; call update_status_badge() once a health check returns."""
    label = qt.QLabel("Server: checking...")
    t = tokens()
    label.setStyleSheet(f"color: {t['TEXT_MUTED']}; font-weight: 600; padding: {SPACING_XS}px;")
    return label


def update_status_badge(label: qt.QLabel, ok: bool) -> None:
    t = tokens()
    color = t["SUCCESS"] if ok else t["DANGER"]
    label.setText("Server: online" if ok else "Server: offline")
    label.setStyleSheet(f"color: {color}; font-weight: 600; padding: {SPACING_XS}px;")


def progress_label() -> qt.QLabel:
    """Where a running job reports what it is doing, next to the Cancel button.

    The status bar alone is not enough: a tool run is minutes of server-side
    inference during which the client has nothing to say, and a panel that
    shows nothing at all reads as frozen. An AMASSS run was cancelled at three
    minutes for exactly that reason -- it was working, and finished 40 seconds
    later.
    """
    label = qt.QLabel("")
    label.setWordWrap(True)
    label.setVisible(False)
    t = tokens()
    label.setStyleSheet(f"color: {t['TEXT_MUTED']}; padding: {SPACING_XS}px;")
    return label
