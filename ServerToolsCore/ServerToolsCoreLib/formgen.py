"""Builds Qt widgets from a server tool's argument schema, and reads them back.

Imports neither `requests` nor anything HTTP — see ARCHITECTURE.md dependency
rule. The server is the single source of truth for the schema: adding a field
to a tool server-side makes it appear here without touching any module code.

File-type arguments (any type accepted by `is_file_type` — "file", "zip_file",
"nifti_file", ...) are skipped by build()/collect(): they are not generic
scalar fields, they get their own row in base_widget's "Inputs" section. The
*widget* for such an input is still built here (`file_widget`), so that every
"schema shape -> Qt widget" decision lives in one file.

So is the translation from the server's vocabulary to the one base_widget acts
on — `file_input_modes`, `auto_file_mode`, `result_kind_for` — for the same
reason: it is all "what does the schema say this panel should be". A module
then declares only what the schema *cannot* say (see file_input_modes).

Some schema types render as several widgets rather than one, so they get a
small Python holder class each (`MultiChoiceGroup`, `FileOrFolderInput`,
`JoystickInput`) instead of a QWidget subclass: PythonQt makes subclassing
awkward, and everything the rest of this module needs fits in a plain object
exposing `container` for layout. The one genuine QWidget subclass, the
joystick pad itself, lives in joystick.py.

Escape hatch: a hand-written .ui can still be used by giving its widgets a Qt
dynamic property named "serverArgName" matching the schema argument name —
collect() will pick them up as if they had been generated. Not used by
SurgMovPred; documented for future modules that need custom layout.
"""

import logging
import os

import ctk
import qt

from . import accepts_folder, argument_types, design, file_extensions_for, is_file_type
from .joystick import JoystickPad

logger = logging.getLogger("ServerToolsCore.formgen")

ARG_NAME_PROPERTY = "serverArgName"

# The collapsible box an argument declaring no `section` goes into — i.e. the
# single box every tool's panel is today.
DEFAULT_SECTION = "Inputs"

# Options per row inside a "tabs" tab. Fixed rather than computed from the
# panel width: the module panel is resizable and a reflow on every drag would
# move check boxes under the user's cursor mid-click.
_TAB_COLUMNS = 4

# Where the leftovers go when `groups` doesn't mention every option. The server
# rejects a group naming an option that doesn't exist, but not the reverse —
# and silently dropping an option would mean the user cannot select something
# the tool offers.
_UNGROUPED_LABEL = "Other"

SELECT_ALL_LABEL = "All"
SELECT_NONE_LABEL = "None"
SELECT_DEFAULT_LABEL = "Default"

# Leads the dropdown of an OPTIONAL scalar `server_selectable` argument, and
# reads back as "" so collectArgs drops the argument entirely and the server
# applies its own rule.
#
# It exists because a QComboBox cannot be empty: `addItems` selects index 0 the
# moment the list arrives, so an optional argument whose schema says "leave
# empty and the server decides" (ALI's `model`, ASO's `landmark_models`) had no
# way to be left empty — the first hosted name was submitted by a user who
# never chose it. For ASO that list is DATA/ASO/models/, which holds reference
# bundles next to weight bundles, so the silent default was routinely a
# reference and the run died on "No CBCT landmark weights found in ...".
#
# Only for OPTIONAL arguments: a required one has no server-side fallback to
# defer to, so offering the entry would only produce a 422.
AUTOMATIC_OPTION = "(automatic — the server chooses)"

# The two browse buttons of an argument accepting a file or a folder. Which of
# the two the user ends up giving is read back from the path, not from these.
BROWSE_FILE_LABEL = "File..."
BROWSE_FOLDER_LABEL = "Folder..."
PATH_PLACEHOLDER = "Select a file or a folder"

# The one-click test-data button at the end of an input row (the original
# extension's "Test Files" / "Download Test file" buttons, now inline). The
# button is only built when the module declares a URL for the argument
# (base_widget.TEST_DATA); the download itself lives in base_widget, this
# module never talks HTTP.
DOWNLOAD_LABEL = "Test data"

# How a volume already open in the scene appears in the input dropdown, next
# to the server-hosted names. Selection kind is decided by index, never by
# parsing this prefix back (see ServerFileInput._selection).
OPEN_VOLUME_PREFIX = "Open volume: "

# Extensions Slicer holds as a scalar volume in the scene. A file argument
# accepting one of these can equally be satisfied by a volume the user already
# has open, exported at upload time (base_widget._prepareOneInputFile).
_VOLUME_EXTENSIONS = {".nii", ".nii.gz", ".nrrd", ".gipl", ".gipl.gz", ".mha", ".mhd"}


def accepts_volume(spec: dict) -> bool:
    """Whether this file argument can be satisfied by a scalar volume loaded
    in the MRML scene. Read off the schema, like every other widget decision:
    a type whose name says volume/nifti, or whose published extensions
    include a volume format. A csv input must never offer scene volumes."""
    if any("volume" in name or "nifti" in name for name in argument_types(spec)):
        return True
    return any(extension in _VOLUME_EXTENSIONS for extension in file_extensions_for(spec))

# `ArgSpec.ui` values on the scalar types (the multichoice ones are LAYOUTS
# below). "slider" turns a bounded int/float into a ctkSliderWidget; "joystick"
# gives a vec2 the 2D pad. Like every presentation hint, an unknown one falls
# back to the plain rendering with a warning: a newer server must never be
# able to break an older client's panel.
SLIDER_UI = "slider"
JOYSTICK_UI = "joystick"


class MultiChoiceGroup:
    """The checkboxes rendered for a `"multichoice"` argument.

    Holds one QCheckBox per option, in the schema's declaration order (the
    order `choices` arrives in — never sorted), and reads back the *complete*
    {option: checked} state. Sending the full state is required, not a
    convenience: see ToolServerClient._stringify for why a missing option is
    not the same as an unchecked one.

    The argument's `description` is rendered as a visible wrapped hint above
    the boxes, not only as a tooltip. A group of check boxes is the one widget
    whose meaning routinely does not fit in its label — ALI's `cbct_regions`
    and `ios_networks` are both always shown and only one applies to any given
    input, and the server says which in its description ("CBCT only: ...").
    A tooltip nobody hovers is not where that belongs.

    **`layout` and `groups` change only where the boxes are put.** `self.boxes`
    is keyed and ordered by `choices` whatever the layout, so `value()`,
    `collect()`, `connect_changed()` and `all_required_filled()` cannot tell
    the four apart — which is what makes the layouts safe to add: a wrong one
    is ugly, never wrong on the wire. See `LAYOUTS` for what each does and the
    server's `ArgSpec.ui` for why they exist at all.
    """

    def __init__(self, choices: dict, description: str = "", layout=None, groups=None):
        self.container = qt.QWidget()
        column = qt.QVBoxLayout(self.container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(design.SPACING_XS)

        if description:
            column.addWidget(design.hint_label(description))

        # The state the server declared, kept for the "Default" button — which
        # is the old ASO module's per-mode `Suggest()` button, now on every
        # multichoice of every tool and with the suggestion living server-side.
        self._defaults = dict(choices)

        builder = _LAYOUT_BUILDERS.get(layout)
        if builder is None:
            if layout is not None:
                logger.warning(
                    "Unknown multichoice layout '%s', falling back to a single column", layout
                )
            builder = _build_flat_boxes
        made = builder(column, choices, groups)

        # Declaration order, whatever order the layout visited the options in.
        self.boxes = {option: made[option] for option in choices}

        if len(choices) > 1:
            column.addWidget(self._selectionToolbar())

    def _selectionToolbar(self):
        """All / None / Default. Cheap here, and the difference between usable
        and not once an argument publishes 130 options: without it, restoring
        the server's suggested selection means remembering and re-ticking it by
        hand."""
        bar = qt.QWidget()
        row = qt.QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(design.SPACING_XS)
        row.addStretch(1)
        for label, action in (
            (SELECT_ALL_LABEL, lambda: self.setAll(True)),
            (SELECT_NONE_LABEL, lambda: self.setAll(False)),
            (SELECT_DEFAULT_LABEL, self.restoreDefaults),
        ):
            button = design.link_button(label)
            button.clicked.connect(action)
            row.addWidget(button)
        return bar

    def setAll(self, checked: bool) -> None:
        for box in self.boxes.values():
            box.setChecked(checked)

    def restoreDefaults(self) -> None:
        for option, box in self.boxes.items():
            box.setChecked(bool(self._defaults.get(option)))

    def value(self) -> dict:
        return {option: box.isChecked() for option, box in self.boxes.items()}

    # -- the slice of the QWidget API build()/base_widget use on a field ----

    def setProperty(self, name, value) -> None:
        self.container.setProperty(name, value)

    def setToolTip(self, text) -> None:
        self.container.setToolTip(text)


def _make_box(option: str, checked) -> qt.QCheckBox:
    box = qt.QCheckBox(option)
    box.setChecked(bool(checked))
    return box


def _grouped(choices: dict, groups) -> list:
    """[(group name, [option, ...])] — the declared groups, then whatever they
    left out. Options keep `choices` order within each group, so a group
    listing them in a different order does not reorder the display."""
    if not groups:
        return [("", list(choices))]

    claimed = {option for options in groups.values() for option in options}
    grouped = [
        (name, [option for option in choices if option in set(options)])
        for name, options in groups.items()
    ]
    leftovers = [option for option in choices if option not in claimed]
    if leftovers:
        grouped.append((_UNGROUPED_LABEL, leftovers))
    return [(name, options) for name, options in grouped if options]


def _build_flat_boxes(column, choices: dict, _groups=None) -> dict:
    """One box per line. The default, and what every tool declaring no `ui`
    gets — unchanged from before layouts existed."""
    boxes = {}
    for option, checked in choices.items():
        boxes[option] = _make_box(option, checked)
        column.addWidget(boxes[option])
    return boxes


def _build_inline_boxes(column, choices: dict, _groups=None) -> dict:
    """One horizontal row. For a handful of short options (ASO's two jaws, its
    eight landmark types) that waste a line each stacked vertically."""
    row_container = qt.QWidget()
    row = qt.QHBoxLayout(row_container)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(design.SPACING_MD)

    boxes = {}
    for option, checked in choices.items():
        boxes[option] = _make_box(option, checked)
        row.addWidget(boxes[option])
    row.addStretch(1)

    column.addWidget(row_container)
    return boxes


def _build_grid_boxes(column, choices: dict, groups=None) -> dict:
    """One row per group, options as columns — the chart layout.

    For options whose *position* carries meaning: ASO asks for teeth "spread
    across the arch", and a column of 32 check boxes is the one layout that
    cannot show whether a selection is spread or clustered.

    Sixteen teeth do not fit in a Slicer module panel, so the grid scrolls
    horizontally rather than being squeezed or wrapped — wrapping an arch onto
    two lines would destroy the very adjacency the layout exists to show. The
    old module did the same (`ASO.ui`'s scrollArea around LayoutSemiIOS_tooth).
    """
    grid_container = qt.QWidget()
    grid = qt.QGridLayout(grid_container)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(design.SPACING_XS)

    boxes = {}
    for row_index, (group_name, options) in enumerate(_grouped(choices, groups)):
        if group_name:
            grid.addWidget(design.hint_label(group_name), row_index, 0)
        for offset, option in enumerate(options):
            boxes[option] = _make_box(option, choices[option])
            grid.addWidget(boxes[option], row_index, offset + 1)

    column.addWidget(_horizontal_scroll(grid_container))
    return boxes


def _build_tabs_boxes(column, choices: dict, groups=None) -> dict:
    """One tab per group, options in a scrollable multi-column grid.

    For a catalog too long to scroll through in one piece: ASO publishes 130
    CBCT landmarks, and the grouping (cranial base / upper / lower) is how the
    people who use them already think about them — the server sends it, so the
    tabs are the server's own grouping rather than a client-side guess.
    """
    tabs = qt.QTabWidget()
    boxes = {}
    for group_name, options in _grouped(choices, groups):
        page = qt.QWidget()
        grid = qt.QGridLayout(page)
        grid.setContentsMargins(design.SPACING_SM, design.SPACING_SM, design.SPACING_SM, design.SPACING_SM)
        grid.setSpacing(design.SPACING_XS)
        for index, option in enumerate(options):
            boxes[option] = _make_box(option, choices[option])
            grid.addWidget(boxes[option], index // _TAB_COLUMNS, index % _TAB_COLUMNS)
        tabs.addTab(_vertical_scroll(page), group_name or _UNGROUPED_LABEL)

    column.addWidget(tabs)
    return boxes


def _horizontal_scroll(widget):
    area = qt.QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setVerticalScrollBarPolicy(qt.Qt.ScrollBarAlwaysOff)
    # Without this the area collapses to a couple of pixels: a QScrollArea's
    # size hint ignores its child, so it has to be told how tall one row of
    # check boxes is.
    area.setMinimumHeight(design.CHART_MIN_HEIGHT)
    return area


def _vertical_scroll(widget):
    area = qt.QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(qt.Qt.ScrollBarAlwaysOff)
    area.setMinimumHeight(design.TABS_MIN_HEIGHT)
    return area


# The server's ArgSpec.ui values. A layout it does not know about falls back to
# the flat column with a warning rather than failing the panel: a presentation
# hint from a newer server must never be able to break an older client.
_LAYOUT_BUILDERS = {
    None: _build_flat_boxes,
    "inline": _build_inline_boxes,
    "grid": _build_grid_boxes,
    "tabs": _build_tabs_boxes,
}

LAYOUTS = tuple(name for name in _LAYOUT_BUILDERS if name)


class JoystickInput:
    """The widgets rendered for a `"vec2"` argument: two numbers set together.

    The two spin boxes ARE the value: `value()` reads them and nothing else.
    The pad (built only when the schema says `ui: "joystick"`) is a second way
    of writing into them (a drag sets both at once) while the boxes remain
    for typing an exact number, the same pairing FlexReg keeps between its
    pads and line edits. Change notification hangs off the boxes alone, so
    every input path (drag, wheel, keys, typing) is one code path.

    A `spring_back` pad is relative: the knob deals out displacements from its
    rest position and the boxes accumulate them (clamped by their own ranges),
    the running total becoming the new base when the gesture ends. Without it
    the pad is absolute and simply mirrors the boxes.
    """

    def __init__(self, x_range=(0.0, 1.0), y_range=(0.0, 1.0), initial=None, step=None,
                 x_axis="X", y_axis="Y", x_labels=None, y_labels=None,
                 spring_back=False, description="", with_pad=True):
        self._syncing = False

        self.container = qt.QWidget()
        column = qt.QVBoxLayout(self.container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(design.SPACING_XS)
        if description:
            column.addWidget(design.hint_label(description))

        row_container = qt.QWidget()
        row = qt.QHBoxLayout(row_container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(design.SPACING_MD)

        x0, y0 = _vec2_initial(initial, x_range, y_range)
        self._base = (x0, y0)

        self.xBox = _axis_spinbox(x_range, step)
        self.yBox = _axis_spinbox(y_range, step)
        self.xBox.setValue(x0)
        self.yBox.setValue(y0)

        self.pad = None
        if with_pad:
            self.pad = JoystickPad(
                x_range=x_range, y_range=y_range, x_step=step, y_step=step,
                x_labels=x_labels, y_labels=y_labels, spring_back=spring_back,
            )
            self.pad.setDefaults(x0, y0)
            self.pad.setValues(x0, y0)
            self.pad.onChanged = self._onPadMoved
            self.pad.onReleased = self._onPadReleased
            row.addWidget(self.pad)

        boxes_container = qt.QWidget()
        boxes = qt.QFormLayout(boxes_container)
        boxes.addRow(design.section_title(x_axis), self.xBox)
        boxes.addRow(design.section_title(y_axis), self.yBox)
        row.addWidget(boxes_container, 1)
        column.addWidget(row_container)

        self.xBox.valueChanged.connect(self._onBoxEdited)
        self.yBox.valueChanged.connect(self._onBoxEdited)

    def value(self) -> list:
        return [self.xBox.value, self.yBox.value]

    def _onPadMoved(self, pad) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            if pad.spring_back:
                # The knob's offset from its rest position is a displacement
                # dealt onto the committed base, not a value of its own.
                self.xBox.setValue(self._base[0] + (pad.value_x - pad.default_x))
                self.yBox.setValue(self._base[1] + (pad.value_y - pad.default_y))
            else:
                self.xBox.setValue(pad.value_x)
                self.yBox.setValue(pad.value_y)
        finally:
            self._syncing = False

    def _onPadReleased(self, _pad) -> None:
        # The gesture's running total becomes the new base; the pad has
        # already sprung home silently.
        self._base = (self.xBox.value, self.yBox.value)

    def _onBoxEdited(self, *_args) -> None:
        if self._syncing:
            return
        self._base = (self.xBox.value, self.yBox.value)
        if self.pad is not None and not self.pad.spring_back:
            self._syncing = True
            try:
                self.pad.setValues(self.xBox.value, self.yBox.value)
            finally:
                self._syncing = False

    # -- the slice of the QWidget API build()/base_widget use on a field ----

    def setProperty(self, name, value) -> None:
        self.container.setProperty(name, value)

    def setToolTip(self, text) -> None:
        self.container.setToolTip(text)


def _axis_spinbox(bounds, step) -> qt.QDoubleSpinBox:
    """The number a pad is showing, read-only.

    The pad IS the input: it sets both axes with one gesture, and the knob sits
    where the point sits on the arch. A box that also accepts typing gives the
    same value two owners and reads as a form to fill in, which is not what the
    original was -- there the numbers report what the pad is doing.
    """
    box = qt.QDoubleSpinBox()
    box.setReadOnly(True)
    box.setButtonSymbols(qt.QAbstractSpinBox.NoButtons)
    box.setFocusPolicy(qt.Qt.NoFocus)
    low, high = sorted((float(bounds[0]), float(bounds[1])))
    box.setRange(low, high)
    box.setDecimals(_decimals_for_step(step))
    if step:
        box.setSingleStep(float(step))
    return box


def _vec2_initial(initial, x_range, y_range):
    """The pair the panel opens at: the declared `initial`, or the centre of
    both axes (a joystick's rest position, and where a spring_back pad deals
    its displacements from)."""
    if isinstance(initial, (list, tuple)) and len(initial) == 2:
        return float(initial[0]), float(initial[1])
    return ((float(x_range[0]) + float(x_range[1])) / 2.0,
            (float(y_range[0]) + float(y_range[1])) / 2.0)


def _decimals_for_step(step, maximum=6) -> int:
    """Decimal places that make `step` representable (0.05 needs 2), with 2
    (the ctk default) when no step is declared."""
    if not step:
        return 2
    step = abs(float(step))
    decimals = 0
    while decimals < maximum and abs(round(step) - step) > 1e-9:
        step *= 10.0
        decimals += 1
    return decimals


class FileOrFolderInput:
    """One input row for a file argument that also accepts a whole folder —
    `types` containing "folder", e.g. example_tool's `input`:
    `["csv_file", "folder"]`.

    HTTP has no notion of a folder, so a folder selection is zipped before
    upload (base_widget._prepareOneInputFile); the server sees an archive,
    extracts it, and strips a lone root directory if there is one — so whether
    the zip holds `cohort/a.csv` or `a.csv` makes no difference.

    **The user never declares which of the two they are providing**: there is
    one path field, and `is_folder()` answers from the path itself. Asking
    first was not just an extra click, it was a source of wrong requests — a
    folder pasted into a field set to "File" was uploaded as if it were one,
    and failed at `open()` with an unhelpful error.

    This is a plain QLineEdit with its own two browse buttons rather than a
    ctkPathLineEdit, and that is forced by ctkPathLineEdit's behavior, not a
    matter of taste. It emits `currentPathChanged` only for input its name
    filters accept, so restricting a picker to `*.csv` — which the schema asks
    for, `types` naming the accepted extensions — silently swallows the change
    signal for **every folder** (measured against Slicer 5.13: with a `*.csv`
    filter, only the `.csv` selections of a file/folder/file/xlsx sequence
    notify; filter order changes nothing). The Apply button would then never
    enable after picking a folder. Driving both dialogs here keeps the file
    dialog filtered by the declared extensions *and* every selection
    observable.
    """

    def __init__(self, extensions=(), with_download=False):
        self._extensions = tuple(extensions)

        self.container = qt.QWidget()
        row_layout = qt.QHBoxLayout(self.container)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(design.SPACING_XS)

        self.pathEdit = qt.QLineEdit()
        self.pathEdit.setPlaceholderText(PATH_PLACEHOLDER)
        self.fileButton = design.compact_button(BROWSE_FILE_LABEL)
        self.folderButton = design.compact_button(BROWSE_FOLDER_LABEL)
        row_layout.addWidget(self.pathEdit, 1)
        row_layout.addWidget(self.fileButton)
        row_layout.addWidget(self.folderButton)

        # Built here so the whole input, test-data button included, stays one
        # line; base_widget connects it (the download itself is HTTP).
        self.downloadButton = None
        if with_download:
            self.downloadButton = design.compact_button(DOWNLOAD_LABEL)
            row_layout.addWidget(self.downloadButton)

        self.fileButton.clicked.connect(self._onBrowseFile)
        self.folderButton.clicked.connect(self._onBrowseFolder)

    @property
    def currentPath(self) -> str:
        """Same name as ctkPathLineEdit's, so base_widget's readiness check
        treats this field like any other path input."""
        return self.pathEdit.text.strip()

    def is_folder(self) -> bool:
        """Whether what the user picked is a folder — read off the filesystem,
        never off a mode the user had to set correctly beforehand."""
        path = self.currentPath
        return bool(path) and os.path.isdir(path)

    def _onBrowseFile(self) -> None:
        path = qt.QFileDialog.getOpenFileName(
            self.container, BROWSE_FILE_LABEL, self.currentPath, ";;".join(name_filters(self._extensions))
        )
        if path:
            self.pathEdit.setText(path)

    def _onBrowseFolder(self) -> None:
        folder = qt.QFileDialog.getExistingDirectory(self.container, BROWSE_FOLDER_LABEL, self.currentPath)
        if folder:
            self.pathEdit.setText(folder)

    # -- the slice of the QWidget API build()/base_widget use on a field ----

    def setProperty(self, name, value) -> None:
        self.container.setProperty(name, value)

    def setToolTip(self, text) -> None:
        self.container.setToolTip(text)


class ServerFileInput:
    """One input row for a file argument that can be satisfied three ways: a
    local file or folder to upload, a file the SERVER already hosts by name
    (`server_selectable`, e.g. ALI's and AMASSS's `input`), or a scalar
    volume already OPEN in the scene (any argument `accepts_volume` says yes
    to).

    Everything sits on ONE line, [sources dropdown][local picker][test data],
    matching the original modules where each input is a single row.

    The dropdown's entries, in order: the upload entry, the open volumes
    (fed by base_widget; formgen never touches the MRML scene), then the
    server-hosted names (`GET /tools/<tool>/data`). Which kind is selected is
    decided by INDEX (`_selection`), never by parsing the text back, so a
    hosted file whose name happens to start with the volume prefix cannot be
    misread.

    On the wire the three are genuinely different: an upload is a multipart
    file part, a server-side selection is a plain form value under the
    argument's own name (the named file never travels in either direction,
    which is the whole point for a test cohort of confidential scans), and an
    open volume is exported to disk at upload time and sent like a local file
    (base_widget._prepareOneInputFile).

    The sources are kept mutually exclusive by clearing the other one, not by
    letting one silently win: picking a dropdown entry empties the path
    field, and typing/browsing a path resets the dropdown. A precedence rule
    the user cannot see is how you end up uploading a file you thought you
    had replaced.

    Rebuilding the dropdown (`setChoices`/`setVolumeChoices`) preserves the
    current selection by text when it is still offered: both lists are
    refreshed on every `enter()`, and a refresh must not silently reset a
    chosen entry to the first one in the list.
    """

    # First entry, and the one that means "nothing chosen here": a combo box
    # cannot express "nothing selected" in a way a user reads as deliberate.
    UPLOAD_OPTION = "Upload my own file..."

    def __init__(self, local, with_download=False):
        self.local = local
        self._syncing = False
        self._server_names = []
        self._volume_names = []

        self.container = qt.QWidget()
        row = qt.QHBoxLayout(self.container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(design.SPACING_XS)

        self.combo = qt.QComboBox()
        # Without this a long hosted name (cohort_10_patients.zip) widens the
        # dropdown until the path field has no room left on the line.
        self.combo.sizeAdjustPolicy = qt.QComboBox.AdjustToMinimumContentsLengthWithIcon
        self.combo.minimumContentsLength = 14
        self.combo.addItems([self.UPLOAD_OPTION])
        row.addWidget(self.combo)
        row.addWidget(row_widget(local), 1)

        # Built here so the whole input, test-data button included, stays one
        # line; base_widget connects it (the download itself is HTTP).
        self.downloadButton = None
        if with_download:
            self.downloadButton = design.compact_button(DOWNLOAD_LABEL)
            row.addWidget(self.downloadButton)

        self.combo.currentTextChanged.connect(self._onComboChoice)
        connect_changed(local, self._onLocalChoice)

    def setChoices(self, names) -> None:
        """Fill the dropdown with the server-hosted names. Called once the
        schema is known and again on every enter(); formgen never talks HTTP
        (see ARCHITECTURE.md dependency rule)."""
        self._server_names = list(names)
        self._rebuild()

    def setVolumeChoices(self, names) -> None:
        """The scalar volumes currently open in the scene, as display names.
        base_widget owns the name-to-node mapping and the refresh triggers;
        this widget only offers the entries."""
        self._volume_names = list(names)
        self._rebuild()

    def _rebuild(self) -> None:
        previous = self.combo.currentText
        # Guarded: clear()+addItems reselects index 0, which would otherwise
        # run the mutual-exclusion sync for a choice the user never made.
        self._syncing = True
        try:
            self.combo.clear()
            entries = [self.UPLOAD_OPTION]
            entries += [OPEN_VOLUME_PREFIX + name for name in self._volume_names]
            entries += self._server_names
            self.combo.addItems(entries)
            if previous in entries:
                self.combo.setCurrentIndex(entries.index(previous))
        finally:
            self._syncing = False

    def _selection(self):
        """("upload" | "volume" | "server", name) for the current entry,
        decided by index so no name can be misparsed."""
        index = self.combo.currentIndex
        if index <= 0:
            return "upload", ""
        if index <= len(self._volume_names):
            return "volume", self._volume_names[index - 1]
        server_index = index - 1 - len(self._volume_names)
        if server_index < len(self._server_names):
            return "server", self._server_names[server_index]
        return "upload", ""

    def server_name(self) -> str:
        """The chosen server-side file name, or "" otherwise."""
        kind, name = self._selection()
        return name if kind == "server" else ""

    def volume_name(self) -> str:
        """The chosen open volume's display name, or "" otherwise."""
        kind, name = self._selection()
        return name if kind == "volume" else ""

    @property
    def currentPath(self) -> str:
        """The LOCAL path to upload: empty while a server file or an open
        volume is chosen, so nothing is read off disk for an argument that is
        already satisfied another way."""
        if self.server_name() or self.volume_name():
            return ""
        return _local_path(self.local)

    def is_folder(self) -> bool:
        checker = getattr(self.local, "is_folder", None)
        return bool(checker()) if checker else False

    def _onComboChoice(self, _text=None) -> None:
        if self._syncing or self.combo.currentIndex <= 0:
            return
        self._syncing = True
        try:
            _set_local_path(self.local, "")
        finally:
            self._syncing = False

    def _onLocalChoice(self, *_args) -> None:
        if self._syncing or not _local_path(self.local):
            return
        self._syncing = True
        try:
            self.combo.setCurrentIndex(0)
        finally:
            self._syncing = False

    # -- the slice of the QWidget API build()/base_widget use on a field ----

    def setProperty(self, name, value) -> None:
        self.container.setProperty(name, value)

    def setToolTip(self, text) -> None:
        self.container.setToolTip(text)


def download_button(widget):
    """The row's test-data button, wherever the composite put it, or None."""
    button = getattr(widget, "downloadButton", None)
    if button is not None:
        return button
    return getattr(getattr(widget, "local", None), "downloadButton", None)


def set_local_path(widget, value: str) -> None:
    """Write a local path into any input-row kind: what base_widget fills in
    once the test data has been downloaded. Writing the local half of a
    ServerFileInput also resets its dropdown, through its own sync."""
    target = widget.local if isinstance(widget, ServerFileInput) else widget
    _set_local_path(target, value)


def _local_path(widget) -> str:
    return (getattr(widget, "currentPath", "") or "").strip()


def _set_local_path(widget, value: str) -> None:
    """Write a path into whichever picker kind `widget` is.

    A FileOrFolderInput drives a plain QLineEdit (see that class for why it is
    not a ctkPathLineEdit); everything else exposes ctkPathLineEdit's writable
    `currentPath`.
    """
    if isinstance(widget, FileOrFolderInput):
        widget.pathEdit.setText(value)
    else:
        widget.currentPath = value


def row_widget(field):
    """The QWidget to put in a form row for `field` — composite fields lay
    several widgets out inside a container."""
    return getattr(field, "container", field)


def label_for(name: str, spec: dict) -> str:
    """The text shown next to an argument's widget.

    **The schema's `label` when it declares one**, so the words a user reads
    are the tool's own — "Scan / Landmark Folder", not something this file
    invented. The fallback prettifies the argument name and is exactly that: a
    fallback for a tool that declares none. It cannot do better than
    "Cbct landmarks" for `cbct_landmarks`, and it has no way to know that ASO's
    `input` is the folder holding both the scans and their landmarks.

    There is ONE rule and it lives here. There used to be two — `build()` used
    the raw schema name while base_widget prettified it — so a single panel
    showed "Reference" and "cbct_landmarks" one above the other.
    """
    declared = (spec.get("label") or "").strip()
    return declared or name.replace("_", " ").capitalize()


def section_of(spec: dict) -> str:
    """The collapsible box this argument belongs in. An argument declaring no
    `section` — every argument of every tool but ASO today — lands in the one
    box a panel has always had, so the grouping is opt-in per tool and no
    existing panel moves."""
    return spec.get("section") or DEFAULT_SECTION


# A section whose arguments are laid out in a grid rather than one per row.
# Declared per ARGUMENT (`section_columns`) because that is the only place the
# schema has to hang a hint, and read back per section: every argument in one
# section must agree, and the first that speaks wins.
#
# FlexReg is why. Its four patch corners are a 2x2 that MIRRORS THE ARCH -- left
# column one side, right column the other, top row anterior -- so a pad's
# position on screen is where that corner is in the mouth. Stacked one per row
# that meaning is gone, and the panel is four identical pads in a column.
def cell_of(name: str, spec: dict) -> str:
    """Which grid cell an argument shares. Its own name when it names none.

    Several arguments describing ONE thing belong together: FlexReg's anterior
    right corner is a tooth number and a position along it, and upstream drew
    them in one box with the pad. One argument per cell puts the four teeth in a
    column and the four pads in another, which is a table of arguments rather
    than a picture of an arch.
    """
    return spec.get("cell") or name


def section_columns(arguments_schema: dict, section: str) -> int:
    """How many columns `section` is laid out in. 1 is one argument per row."""
    for spec in arguments_schema.values():
        if section_of(spec) == section:
            declared = spec.get("section_columns")
            if declared:
                try:
                    return max(1, int(declared))
                except (TypeError, ValueError):
                    return 1
    return 1


def sections_of(arguments_schema: dict, extra=()) -> list:
    """Every distinct section a tool's arguments name, in the order they are
    first mentioned — the schema's declaration order, which is the tool
    author's intended reading order. `extra` names boxes the client adds on its
    own (the output folder), appended unless an argument already claimed them.
    """
    ordered = []
    for spec in arguments_schema.values():
        name = section_of(spec)
        if name not in ordered:
            ordered.append(name)
    for name in extra:
        if name not in ordered:
            ordered.append(name)
    return ordered


def is_visible(spec: dict, values: dict) -> bool:
    """Whether `visible_when` is satisfied by the panel's current values.

    `{"modality": "CBCT", "automation": "Fully-Automated"}` — every entry must
    match, and a tuple/list of values means "any of these". An argument
    declaring nothing is always visible.

    A controlling argument absent from `values` counts as NOT matching. That
    only happens when the schema could not be fetched (so the panel holds an
    error, not a form) or when a server declares a `visible_when` naming an
    argument it doesn't publish — which its own check_schema rejects at boot.
    Hiding is the safe answer either way: a field whose precondition cannot be
    evaluated is a field the user cannot fill in meaningfully.
    """
    # `hidden` is not a condition: it is never rendered, whatever the panel
    # holds. It carries the arguments a clinician has no business being asked
    # -- which CUDA device, nnUNet's tile step size -- set by whoever deploys
    # the server. The tool still declares them and still applies its own
    # defaults; the client simply does not ask.
    if spec.get("hidden"):
        return False

    conditions = spec.get("visible_when")
    if not conditions:
        return True
    for other_name, expected in conditions.items():
        if other_name not in values:
            return False
        wanted = expected if isinstance(expected, (list, tuple)) else (expected,)
        if values[other_name] not in wanted:
            return False
    return True


def allowed_options(spec: dict, values: dict):
    """The options a choice argument may offer, given what the panel holds.

    None means "no rule, offer them all". `visible_when` can only show or hide
    a whole field; this narrows one that stays. AREG's three automation modes
    are all real, but IOS has no "Oriented + Fully-Automated" — offering it and
    refusing the run at the end is the worst of both.
    """
    rules = spec.get("options_when")
    if not rules:
        return None
    allowed = None
    for other_name, by_value in rules.items():
        chosen = values.get(other_name)
        if chosen is None or chosen not in by_value:
            # Nothing said about this state: a rule that cannot be evaluated
            # must not silently empty the box.
            continue
        permitted = list(by_value[chosen])
        allowed = permitted if allowed is None else [o for o in allowed if o in permitted]
    return allowed


def controlling_arguments(arguments_schema: dict) -> set:
    """Every argument the panel has to re-evaluate on — the ones some other
    argument's visibility, or its set of options, depends on."""
    return {
        other_name
        for spec in arguments_schema.values()
        for key in ("visible_when", "options_when")
        for other_name in (spec.get(key) or {})
    }


def build(arguments_schema: dict, layout, sections=None, rows=None) -> dict:
    """Add one row per non-file argument. Returns {arg_name: widget}.

    `layout` is a qt.QFormLayout — the single-box behavior, kept as the default
    so a caller that knows nothing about sections is unaffected. `sections` is
    {section name: QFormLayout}; when given, each argument goes to the layout
    its `section` names and `layout` is only the fallback for a section the
    caller didn't create.

    `rows`, if given, is filled with `{arg_name: (label, field)}` — the two
    widgets a caller has to show or hide together to make a row appear or
    disappear. An out-parameter rather than a second return value so the
    signature stays what every existing caller and test expects; the labels are
    created in here, and a caller cannot recover a QFormLayout's label for a
    field reliably across PythonQt versions.
    """
    widgets = {}
    # {(layout, cell name): the QWidget holding that cell}, so several arguments
    # naming one cell stack inside it instead of taking a cell each.
    grid_cells = {}
    for name, spec in arguments_schema.items():
        if is_file_type(spec.get("type", "")):
            continue

        widget = _make_widget(name, spec)
        widget.setProperty(ARG_NAME_PROPERTY, name)
        description = spec.get("description")
        if description:
            widget.setToolTip(description)

        text = label_for(name, spec)
        label = design.required_label(text) if spec.get("required") else design.section_title(text)
        target = (sections or {}).get(section_of(spec), layout)
        field = row_widget(widget)
        if hasattr(target, "addRow"):
            target.addRow(label, field)
        else:
            # A grid section: the caller handed a QGridLayout instead, and the
            # label goes above its field rather than beside it, so a 2x2 of pads
            # reads as a 2x2 rather than as four labelled rows.
            cell = qt.QWidget()
            stack = qt.QVBoxLayout(cell)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.addWidget(label)
            stack.addWidget(field)
            # Read back from the schema, never stored on the layout: PythonQt
            # forbids creating an attribute on a C++ object, so `grid.columns =
            # 2` fails with "creating new attributes on C++ objects is not
            # allowed" and takes the whole panel down.
            columns = section_columns(arguments_schema, section_of(spec))
            key = (id(target), cell_of(name, spec))
            holder = grid_cells.get(key)
            if holder is None:
                holder = qt.QWidget()
                qt.QVBoxLayout(holder).setContentsMargins(0, 0, 0, 0)
                placed = len(
                    [k for k in grid_cells if k[0] == id(target)])
                target.addWidget(holder, placed // columns, placed % columns)
                grid_cells[key] = holder
            holder.layout().addWidget(cell)
        widgets[name] = widget
        if rows is not None:
            rows[name] = (label, field)
    return widgets


def _make_widget(name: str, spec: dict):
    arg_type = spec.get("type", "str")

    # A scalar argument flagged server_selectable (e.g. SurgMovPred's
    # "model": the *name* of a model hosted on the server) is a choice among
    # server-side files, not free text: render a dropdown. base_widget
    # populates it from GET /tools/{tool}/data once the schema is known —
    # formgen itself never talks HTTP (dependency rule, see ARCHITECTURE.md).
    # Checked before the type so a server-filled dropdown is never overwritten
    # with a schema-declared choice list.
    if spec.get("server_selectable"):
        return qt.QComboBox()

    # `initial` is the scalar counterpart of a choice argument's `choices`: the
    # value the SERVER wants the widget to start at. It matters because collect()
    # always sends every widget, so a field the user never touched still travels
    # — a spin box left at Qt's own 0 sent 0, never letting the tool's own
    # default apply. None means the tool declared none; leave Qt's default then.
    initial = spec.get("initial")

    if arg_type == "str":
        widget = qt.QLineEdit()
        if initial is not None:
            widget.setText(str(initial))
        return widget
    if arg_type == "int":
        return _make_numeric_widget(name, spec, integer=True, initial=initial)
    if arg_type == "float":
        return _make_numeric_widget(name, spec, integer=False, initial=initial)
    if arg_type == "bool":
        widget = qt.QCheckBox()
        if initial is not None:
            widget.setChecked(bool(initial))
        return widget
    if arg_type == "vec2":
        return _make_vec2_widget(name, spec)
    if arg_type == "choice":
        return _make_choice_widget(name, spec)
    if arg_type == "multichoice":
        return MultiChoiceGroup(
            _choices(name, spec),
            spec.get("description", ""),
            layout=spec.get("ui"),
            groups=spec.get("groups"),
        )
    if is_file_type(arg_type):
        return file_widget(spec)

    logger.warning("Unknown argument type '%s' for '%s', falling back to QLineEdit", arg_type, name)
    return qt.QLineEdit()


def _make_numeric_widget(name: str, spec: dict, integer: bool, initial):
    """An int/float argument. `ui: "slider"` (with min/max declared) renders
    the combined slider+spinbox; otherwise a spin box whose range and step
    still honour any declared bounds. min/max alone constrain the field, they
    never switch the widget kind, so a bound added server-side for validation
    cannot silently turn a spin box into a slider."""
    ui = spec.get("ui")
    if ui == SLIDER_UI:
        slider = _make_slider_widget(name, spec, integer)
        if slider is not None:
            return slider
    elif ui is not None:
        logger.warning(
            "Unknown %s ui '%s' for '%s', falling back to a spin box",
            "int" if integer else "float", ui, name,
        )

    if integer:
        widget = qt.QSpinBox()
        widget.setRange(
            -2147483648 if spec.get("min") is None else int(spec["min"]),
            2147483647 if spec.get("max") is None else int(spec["max"]),
        )
        if spec.get("step") is not None:
            widget.setSingleStep(int(spec["step"]))
        if initial is not None:
            widget.setValue(int(initial))
        return widget

    widget = qt.QDoubleSpinBox()
    widget.setRange(
        -1e12 if spec.get("min") is None else float(spec["min"]),
        1e12 if spec.get("max") is None else float(spec["max"]),
    )
    declared = spec.get("decimals")
    widget.setDecimals(int(declared) if declared is not None else 6)
    if spec.get("step") is not None:
        widget.setSingleStep(float(spec["step"]))
    if initial is not None:
        widget.setValue(float(initial))
    return widget


def _make_slider_widget(name: str, spec: dict, integer: bool):
    """A bounded int/float rendered as a ctkSliderWidget, the slider + spin
    box combination GreedyReg's manual-alignment rows use. Returns None when
    the schema asked for a slider without both bounds: an unbounded slider has
    no geometry, so the argument falls back to a plain spin box rather than
    failing the panel."""
    minimum, maximum = spec.get("min"), spec.get("max")
    if minimum is None or maximum is None:
        logger.warning(
            "Argument '%s' asks for ui \"slider\" but declares no min/max bounds, "
            "falling back to a spin box", name,
        )
        return None

    widget = ctk.ctkSliderWidget()
    widget.minimum = float(minimum)
    widget.maximum = float(maximum)
    step = spec.get("step")
    if integer:
        widget.decimals = 0
        widget.singleStep = float(step) if step is not None else 1.0
    else:
        declared = spec.get("decimals")
        widget.decimals = int(declared) if declared is not None else _decimals_for_step(step)
        if step is not None:
            widget.singleStep = float(step)
    initial = spec.get("initial")
    if initial is not None:
        widget.value = float(initial)
    return widget


def _make_vec2_widget(name: str, spec: dict):
    """A `"vec2"` argument: two numbers set together. `ui: "joystick"` adds
    the 2D pad next to the boxes; any other hint falls back to the boxes
    alone, same rule as the multichoice layouts: a newer server's presentation
    hint must never break an older client."""
    ui = spec.get("ui")
    if ui is not None and ui != JOYSTICK_UI:
        logger.warning("Unknown vec2 ui '%s' for '%s', falling back to two spin boxes", ui, name)
    return JoystickInput(
        x_range=_axis_range(name, spec, "x_range"),
        y_range=_axis_range(name, spec, "y_range"),
        initial=spec.get("initial"),
        step=spec.get("step"),
        x_axis=spec.get("x_label") or "X",
        y_axis=spec.get("y_label") or "Y",
        x_labels=_axis_labels(spec.get("x_labels")),
        y_labels=_axis_labels(spec.get("y_labels")),
        spring_back=bool(spec.get("spring_back")),
        description=spec.get("description", ""),
        with_pad=ui == JOYSTICK_UI,
    )


def _axis_range(name: str, spec: dict, key: str):
    """One vec2 axis. Index 0 is the left/bottom end, index 1 the right/top,
    so declaring the bounds inverted mirrors the axis (see JoystickPad)."""
    declared = spec.get(key)
    if isinstance(declared, (list, tuple)) and len(declared) == 2 and declared[0] != declared[1]:
        return float(declared[0]), float(declared[1])
    if declared is not None:
        logger.warning("Argument '%s' declares an invalid %s %r, using (0, 1)", name, key, declared)
    return (0.0, 1.0)


def _axis_labels(declared):
    if isinstance(declared, (list, tuple)) and len(declared) == 2:
        return tuple(str(label) for label in declared)
    return None


def _make_choice_widget(name: str, spec: dict):
    """A `"choice"` argument: one option among `choices`, whose single true
    entry is the server's declared default."""
    choices = _choices(name, spec)
    options = list(choices)

    widget = qt.QComboBox()
    widget.addItems(options)
    selected = [option for option, on in choices.items() if on]
    if selected:
        widget.setCurrentIndex(options.index(selected[0]))
    return widget


# What each single-kind file-input mode means for a ctkPathLineEdit. There is
# deliberately no "file_or_folder" entry: an argument accepting both is a
# FileOrFolderInput, for the reasons spelled out in that class.
_PATH_FILTERS = {
    "single_file": ctk.ctkPathLineEdit.Files,
    "folder_zip": ctk.ctkPathLineEdit.Dirs,
}


def path_widget(extensions=(), mode: str = "single_file"):
    """A ctkPathLineEdit for one file-input mode, restricted to `extensions`
    where that applies.

    A ctkPathLineEdit is configured **once, here, at construction**, and never
    touched again: re-assigning `nameFilters` on a live one corrupts it and
    takes Slicer down with it — reproduced against Slicer 5.13, and the reason
    the mode is a constructor argument rather than something the widget
    switches between later. Hence also the `if`: an unrestricted picker is left
    with its default rather than handed an empty list.
    """
    widget = ctk.ctkPathLineEdit()
    widget.filters = _PATH_FILTERS.get(mode, ctk.ctkPathLineEdit.Files)
    if mode != "folder_zip" and extensions:
        widget.nameFilters = name_filters(extensions)
    return widget


def auto_file_mode(spec: dict) -> str:
    """Which kind of picker a file argument gets, from what its `types` accept.

    The general rule, in one place: an argument accepting "folder" may be given
    a whole folder (zipped before upload); one accepting a file type as well
    gets the choice between the two. Returns a base_widget FILE_INPUTS mode,
    because the answer is needed twice — to build the widget, and again at
    upload time to know whether to zip (see base_widget._prepareOneInputFile).
    """
    if not accepts_folder(spec):
        return "single_file"
    if any(is_file_type(type_name) for type_name in argument_types(spec)):
        return "file_or_folder"
    return "folder_zip"


def file_input_modes(arguments_schema: dict, overrides=None) -> dict:
    """`{argument_name: mode}` for every file argument the client provides, in
    schema order.

    **Which arguments those are is the schema's answer, not a module's**: every
    file-typed argument gets an input row. A module's `FILE_INPUTS` is merged
    on top and only has to say what the schema cannot express —

    - `"volume_node"` / `"model_node"`: filled from a node in the MRML scene
      rather than from disk, and exported at upload time. The server does not
      know a scene exists;
    - a forced `"folder_zip"`/`"single_file"`: SurgMovPred's `input` is typed
      `zip_file`, and the module still wants to hand the user a folder picker
      and zip it client-side. "Give me a zip" is the contract; "let them pick a
      folder" is an ergonomics decision that lives here;
    - `"none"`: an optional file argument this module deliberately doesn't
      offer.

    Everything else stays `"auto"` and is resolved by `auto_file_mode`.
    """
    modes = {
        name: "auto"
        for name, spec in arguments_schema.items()
        if is_file_type(spec.get("type", ""))
    }
    modes.update(overrides or {})

    resolved = {}
    for name, mode in modes.items():
        if mode == "none":
            continue
        resolved[name] = auto_file_mode(arguments_schema.get(name, {})) if mode == "auto" else mode
    return resolved


# How a tool's server-side `output_kind` maps onto the client's RESULT_KIND.
_RESULT_KIND_FOR_OUTPUT = {
    "text": "text",
    "segmentation": "segmentation",
    "file": "save_as",
    "files": "save_as",
}


def result_kind_for(output_kind, declared=None) -> str:
    """The client's RESULT_KIND for a tool's declared `output_kind`.

    Three of the server's four output kinds settle the question on their own:
    `text` is text, `segmentation` is a segmentation, and `files` can only be
    saved (a zip of several files cannot become one MRML node).

    **`file` is the one genuinely ambiguous case**: the server says a single
    file comes back, it cannot say whether that file is meant to be loaded into
    the scene as a volume or as a mesh, or just written to disk — that is MRML
    knowledge, and the server has no business holding it. It defaults to
    `save_as`, and a module wanting the result loaded declares
    `RESULT_KIND = "volume"` / `"model"`. A declared value always wins.
    """
    return declared or _RESULT_KIND_FOR_OUTPUT.get(output_kind, "text")


def file_widget(spec: dict, mode: str = "auto", with_download: bool = False):
    """The picker for a file argument. `mode` defaults to the schema-driven
    rule above; base_widget passes an explicit one for what the schema cannot
    express (or to force a single selection kind).

    `with_download` adds the inline test-data button: the module declared a
    TEST_DATA URL for this argument. The button is created here so the row
    stays one line; base_widget wires it (the download itself is HTTP).

    Kept here (rather than in base_widget) so every "schema shape -> Qt
    widget" decision lives in one file; `build()` itself never emits one (see
    the module docstring and FILE_INPUTS).
    """
    if mode == "auto":
        mode = auto_file_mode(spec)

    # One dropdown serves both extra sources: a file the server can provide
    # by name (server_selectable), and a volume already open in the scene
    # (accepts_volume). Only file-typed arguments reach here: a SCALAR
    # server_selectable argument (a model, which must never leave the server)
    # is a plain combo box built by _make_widget, with no local picker at all.
    wrap = bool(spec.get("server_selectable")) or accepts_volume(spec)

    extensions = file_extensions_for(spec)
    if mode == "file_or_folder":
        local = FileOrFolderInput(extensions, with_download=with_download and not wrap)
    else:
        local = path_widget(extensions, mode)
        if with_download and not wrap:
            logger.warning(
                "Test data declared for an argument whose bare path picker "
                "cannot host the button; ignoring"
            )

    if wrap:
        return ServerFileInput(local, with_download=with_download)
    return local


def _choices(name: str, spec: dict) -> dict:
    """`choices` is a {option: initially_selected} dict, and its key order is
    the declaration order — preserved as-is, never sorted."""
    choices = spec.get("choices")
    if not choices:
        logger.warning("Argument '%s' is a '%s' but declares no choices", name, spec.get("type"))
        return {}
    return choices


def name_filters(extensions) -> list:
    """Qt name filters for a file picker restricted to `extensions` (an empty
    list — no restriction — when it is empty)."""
    if not extensions:
        return []
    patterns = " ".join(f"*{extension}" for extension in extensions)
    return [f"Supported files ({patterns})", "All files (*)"]


def collect(arg_widgets: dict) -> dict:
    return {name: _read_widget(widget) for name, widget in arg_widgets.items()}


def _read_widget(widget):
    if isinstance(widget, MultiChoiceGroup):
        # The complete state of every box, including the unchecked ones — the
        # server reads what it receives as the selection itself. Encoding it
        # for the wire is client.py's job (JSON, never the `a,b` shortcut).
        return widget.value()
    if isinstance(widget, JoystickInput):
        # Both numbers, as a two-element list; client.py sends it as JSON.
        return widget.value()
    if isinstance(widget, ctk.ctkSliderWidget):
        # ctk reports a double whatever `decimals` says; an integer slider
        # (decimals == 0) reads back as the int the server declared.
        value = widget.value
        return int(round(value)) if widget.decimals == 0 else value
    if isinstance(widget, qt.QCheckBox):
        return widget.isChecked()
    if isinstance(widget, qt.QComboBox):
        # The selected option's name for a "choice" argument, sent in clear.
        # "" while a server-side list hasn't been loaded (or is empty) — which
        # keeps all_required_filled() False and the Apply button disabled.
        #
        # The "(automatic)" entry reads back as "" so the argument is dropped
        # rather than sent: matched on the text because that is what the entry
        # IS here, and no server-side file name nor `choices` option can
        # collide with it (both come from the server; one is a file name, the
        # other a declared option, and this string is neither).
        text = widget.currentText
        return "" if text == AUTOMATIC_OPTION else text
    if isinstance(widget, (qt.QSpinBox, qt.QDoubleSpinBox)):
        return widget.value
    if isinstance(widget, ctk.ctkPathLineEdit):
        return widget.currentPath
    if isinstance(widget, qt.QLineEdit):
        return widget.text
    raise TypeError(f"Don't know how to read value from widget {widget!r}")


def all_required_filled(arg_widgets: dict, arguments_schema: dict, hidden=()) -> bool:
    """Whether every required scalar argument holds a value.

    A `hidden` argument is skipped: it is not sent (see base_widget.collectArgs),
    so the server applies its default and an empty widget behind a hidden row
    must not be able to disable Apply forever with nothing on screen to explain
    why. No tool declares a required argument under a `visible_when` today, and
    this is what keeps that from becoming a dead-locked panel if one does.
    """
    for name, spec in arguments_schema.items():
        if is_file_type(spec.get("type", "")) or not spec.get("required") or name in hidden:
            continue
        widget = arg_widgets.get(name)
        if widget is None:
            return False
        value = _read_widget(widget)
        # A multichoice reads as a dict and is always "filled": every box
        # unchecked is a meaningful selection, not a missing value.
        if value in ("", None):
            return False
    return True


def connect_changed(widget, callback) -> None:
    if isinstance(widget, MultiChoiceGroup):
        for box in widget.boxes.values():
            box.toggled.connect(callback)
    elif isinstance(widget, ServerFileInput):
        # Either half can satisfy the argument, so either half changing must
        # re-evaluate whether Apply can be enabled.
        widget.combo.currentTextChanged.connect(callback)
        connect_changed(widget.local, callback)
    elif isinstance(widget, FileOrFolderInput):
        # Both buttons write into the same field, so one connection covers
        # browsing either kind as well as typing or pasting a path.
        widget.pathEdit.textChanged.connect(callback)
    elif isinstance(widget, JoystickInput):
        # The pad writes into the spin boxes (see JoystickInput), so the two
        # boxes cover every input path: drag, wheel, keys and typing.
        widget.xBox.valueChanged.connect(callback)
        widget.yBox.valueChanged.connect(callback)
    elif isinstance(widget, ctk.ctkSliderWidget):
        widget.valueChanged.connect(callback)
    elif isinstance(widget, qt.QCheckBox):
        widget.toggled.connect(callback)
    elif isinstance(widget, qt.QComboBox):
        widget.currentTextChanged.connect(callback)
    elif isinstance(widget, (qt.QSpinBox, qt.QDoubleSpinBox)):
        widget.valueChanged.connect(callback)
    elif isinstance(widget, ctk.ctkPathLineEdit):
        widget.currentPathChanged.connect(callback)
    elif isinstance(widget, qt.QLineEdit):
        widget.textChanged.connect(callback)
    else:
        logger.warning("Don't know how to connect change signal for widget %r", widget)
