"""Minimal stand-ins for `qt`, `ctk` and `slicer`, so the GUI-facing parts of
ServerToolsCoreLib can be unit-tested outside Slicer.

Only what `formgen` (and the `design` module it pulls in) actually touches is
implemented - enough to assert *which* widgets a schema produces, in which
order, with which initial state, and what they read back as. This obviously
tests the schema-to-widget logic, not Qt itself; the Qt calls it makes are the
same ones the previously shipped widgets already used.

Call `install()` before importing ServerToolsCoreLib.formgen.
"""

import sys
import types


class Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class QObject:
    def __init__(self, *_args, **_kwargs):
        self._properties = {}
        self._tooltip = ""
        self._stylesheet = ""
        self._visible = True

    def setVisible(self, visible):
        self._visible = bool(visible)

    def isVisible(self):
        return self._visible

    def setEnabled(self, enabled):
        self._enabled = bool(enabled)

    def setProperty(self, name, value):
        self._properties[name] = value

    def property(self, name):
        return self._properties.get(name)

    def setToolTip(self, text):
        self._tooltip = text

    def toolTip(self):
        return self._tooltip

    def setStyleSheet(self, sheet):
        self._stylesheet = sheet

    def setCursor(self, _cursor):
        pass

    def setMinimumHeight(self, height):
        self._minimum_height = height

    def setFixedSize(self, width, height):
        self._fixed_size = (width, height)

    def setFocusPolicy(self, _policy):
        pass

    def update(self):
        pass


class QWidget(QObject):
    def __init__(self, parent=None):
        QObject.__init__(self)
        self.parent = parent
        self.layout = None


class QLayout(QObject):
    def __init__(self, parent=None):
        QObject.__init__(self)
        self.widgets = []
        if parent is not None:
            parent.layout = self

    def setContentsMargins(self, *_margins):
        pass

    def setSpacing(self, _spacing):
        pass

    def addWidget(self, widget, stretch=0):
        self.widgets.append(widget)

    def __call__(self):
        """`widget.layout` is a METHOD in Qt and an attribute here.

        These tests read it as an attribute throughout, and formgen calls
        `holder.layout()` on a grid cell. Making a layout callable lets both
        spellings reach the same object instead of forcing every test to pick
        a side.
        """
        return self


class QVBoxLayout(QLayout):
    pass


class QHBoxLayout(QLayout):
    def addStretch(self, _stretch=0):
        pass


class QGridLayout(QLayout):
    """Records the (row, column) each widget was placed at - which is the whole
    point of the "grid" multichoice layout: a chart is only a chart if the
    positions are right (see formgen._build_grid_boxes)."""

    def __init__(self, parent=None):
        QLayout.__init__(self, parent)
        self.cells = {}  # {(row, column): widget}

    def addWidget(self, widget, row=0, column=0, *_args):
        self.widgets.append(widget)
        self.cells[(row, column)] = widget


class QScrollArea(QWidget):
    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.widget = None
        self.widgetResizable = False
        self.verticalScrollBarPolicy = None
        self.horizontalScrollBarPolicy = None

    def setWidget(self, widget):
        self.widget = widget

    def setWidgetResizable(self, resizable):
        self.widgetResizable = resizable

    def setVerticalScrollBarPolicy(self, policy):
        self.verticalScrollBarPolicy = policy

    def setHorizontalScrollBarPolicy(self, policy):
        self.horizontalScrollBarPolicy = policy


class QTabWidget(QWidget):
    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.tabs = []  # [(title, widget)]

    def addTab(self, widget, title):
        self.tabs.append((title, widget))


class QCursor:
    def __init__(self, shape=0):
        self.shape = shape


class Qt:
    # A read-only box takes no focus: it reports, it does not accept.
    NoFocus = 0
    """The Qt namespace enum values design/formgen/joystick reach for."""

    ScrollBarAlwaysOff = 1
    ScrollBarAsNeeded = 0
    PointingHandCursor = 13
    StrongFocus = 11
    AlignCenter = 0x84
    ControlModifier = 0x04000000
    ShiftModifier = 0x02000000
    Key_Left = 0x01000012
    Key_Up = 0x01000013
    Key_Right = 0x01000014
    Key_Down = 0x01000015


class QFileDialog:
    """Test seam: `next_file`/`next_directory` are what the static helpers
    return, so a test can drive the browse buttons without a real (modal)
    dialog, and inspect the arguments they were called with - the file
    dialog's filter string among them."""

    next_file = ""
    next_directory = ""
    last_open_file_args = None
    last_existing_directory_args = None

    @staticmethod
    def getOpenFileName(*args, **_kwargs):
        QFileDialog.last_open_file_args = args
        return QFileDialog.next_file

    @staticmethod
    def getExistingDirectory(*args, **_kwargs):
        QFileDialog.last_existing_directory_args = args
        return QFileDialog.next_directory


class QFormLayout(QLayout):
    def __init__(self, parent=None):
        QLayout.__init__(self, parent)
        self.rows = []  # [(label, widget)]

    def addRow(self, label, widget):
        self.rows.append((label, widget))
        self.widgets.append(widget)


class QLabel(QObject):
    def __init__(self, text=""):
        QObject.__init__(self)
        self.text = text

    def setText(self, text):
        self.text = text

    def setWordWrap(self, _wrap):
        pass


class QPushButton(QObject):
    def __init__(self, text=""):
        QObject.__init__(self)
        self.text = text
        self.clicked = Signal()
        self._checkable = False

    def setCheckable(self, checkable):
        self._checkable = bool(checkable)


class QLineEdit(QObject):
    def __init__(self, text=""):
        QObject.__init__(self)
        self._text = text
        self.placeholderText = ""
        self.textChanged = Signal()

    def setPlaceholderText(self, text):
        self.placeholderText = text

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value
        self.textChanged.emit(value)

    def setText(self, value):
        self.text = value


class QCheckBox(QObject):
    def __init__(self, text=""):
        QObject.__init__(self)
        self.text = text
        self._checked = False
        self.toggled = Signal()

    def setChecked(self, checked):
        self._checked = bool(checked)
        self.toggled.emit(self._checked)

    def isChecked(self):
        return self._checked


class QComboBox(QObject):
    AdjustToMinimumContentsLengthWithIcon = 2

    def __init__(self):
        QObject.__init__(self)
        self._items = []
        self._data = []
        self._index = -1
        self.sizeAdjustPolicy = 0
        self.minimumContentsLength = 0
        self.currentTextChanged = Signal()
        self.currentIndexChanged = Signal()

    def addItems(self, items):
        self._items.extend(items)
        self._data.extend([None] * len(items))
        if self._index < 0 and self._items:
            self.setCurrentIndex(0)

    def addItem(self, item, userData=None):
        """`userData` is what a combo carries besides its label.

        Real Qt has always had it; this stub did not, so a panel storing the
        value behind each entry — an address, an id, anything not fit to show
        — could not be tested at all. Optional, so every existing caller is
        unaffected.
        """
        self.addItems([item])
        self._data[-1] = userData

    def itemData(self, index):
        if 0 <= index < len(self._data):
            return self._data[index]
        return None

    def clear(self):
        self._items = []
        self._data = []
        self._index = -1

    @property
    def count(self):
        # A property, not a method: PythonQt exposes a Qt property whose getter
        # shares its name as an attribute, and it shadows the method - real
        # Slicer raises "'int' object is not callable" on `combo.count()`.
        return len(self._items)

    def itemText(self, index):
        return self._items[index]

    @property
    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        self._index = index
        # Both, as Qt does. A panel that reacts to the SELECTION rather than to
        # the label has to connect currentIndexChanged — two entries can show
        # the same text, and currentTextChanged does not fire between them.
        self.currentIndexChanged.emit(index)
        self.currentTextChanged.emit(self.currentText)

    @property
    def currentText(self):
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return ""

    def setCurrentText(self, text):
        self.setCurrentIndex(self._items.index(text))


class QSpinBox(QObject):
    def __init__(self):
        QObject.__init__(self)
        self.value = 0
        self.minimum = 0
        self.maximum = 99
        self.singleStep = 1
        self.valueChanged = Signal()

    def setRange(self, minimum, maximum):
        self.minimum, self.maximum = minimum, maximum

    def setSingleStep(self, step):
        self.singleStep = step

    def setValue(self, value):
        # Clamped like the real widget: JoystickInput's spring-back mode
        # relies on the boxes clamping the accumulated displacement.
        self.value = min(max(value, self.minimum), self.maximum)
        self.valueChanged.emit(self.value)


class QDoubleSpinBox(QSpinBox):
    def __init__(self):
        QSpinBox.__init__(self)
        self.value = 0.0
        self.decimals = 2

    def setDecimals(self, decimals):
        self.decimals = decimals

    def setReadOnly(self, readOnly):
        self.readOnly = readOnly

    def setButtonSymbols(self, symbols):
        self.buttonSymbols = symbols


class QAbstractSpinBox:
    """Only the enum formgen names: a read-only box hides its arrows."""

    NoButtons = 2


class QPalette:
    Window = 0


class ctkPathLineEdit(QObject):
    """Counts assignments to `filters`/`nameFilters`.

    Reconfiguring a real, live ctkPathLineEdit corrupts it and crashes Slicer
    (see formgen.path_widget); the counters let a test assert each picker is
    configured exactly once, at construction, and never again.
    """

    Files = 1
    Dirs = 2

    def __init__(self):
        QObject.__init__(self)
        self._path = ""
        self._filters = ctkPathLineEdit.Files
        self._name_filters = []
        self.filterAssignments = 0
        self.nameFilterAssignments = 0
        self.currentPathChanged = Signal()

    @property
    def filters(self):
        return self._filters

    @filters.setter
    def filters(self, value):
        self._filters = value
        self.filterAssignments += 1

    @property
    def nameFilters(self):
        return self._name_filters

    @nameFilters.setter
    def nameFilters(self, value):
        self._name_filters = value
        self.nameFilterAssignments += 1

    @property
    def currentPath(self):
        return self._path

    @currentPath.setter
    def currentPath(self, value):
        self._path = value
        self.currentPathChanged.emit(value)


class ctkCollapsibleButton(QWidget):
    def __init__(self):
        QWidget.__init__(self)
        self.text = ""


class ctkSliderWidget(QObject):
    """PythonQt exposes minimum/maximum/decimals/singleStep/value as writable
    properties. `value` clamps into [minimum, maximum] like the real widget."""

    def __init__(self):
        QObject.__init__(self)
        self.minimum = 0.0
        self.maximum = 99.0
        self.decimals = 2
        self.singleStep = 1.0
        self.pageStep = 1.0
        self._value = 0.0
        self.valueChanged = Signal()

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, new_value):
        self._value = min(max(float(new_value), self.minimum), self.maximum)
        self.valueChanged.emit(self._value)

    def setValue(self, new_value):
        self.value = new_value


class qMRMLNodeComboBox(QObject):
    """The scene-node picker a `volume_node`/`model_node` row is built from.

    Enough of it to be filled and read: which classes it offers, which node is
    current, and the signal a panel connects to. `nodeTypes` and `noneEnabled`
    are writable properties under PythonQt, as they are here.
    """

    def __init__(self):
        QObject.__init__(self)
        self.nodeTypes = []
        self.noneEnabled = True
        self.scene = None
        self._node = None
        self.currentNodeChanged = Signal()

    def setMRMLScene(self, scene):
        self.scene = scene

    def setCurrentNode(self, node):
        self._node = node
        self.currentNodeChanged.emit(node)

    def currentNode(self):
        return self._node


def install():
    """Register the fake `qt`, `ctk` and `slicer` modules in sys.modules.

    `slicer` is deliberately empty: `design.is_dark_mode()` reaches for
    `slicer.app.palette()` inside a try/except, so the missing attribute makes
    it fall back to the light palette - which is all these tests need.
    """
    qt = types.ModuleType("qt")
    for name, value in globals().items():
        if name.startswith("Q") or name == "Signal":
            setattr(qt, name, value)

    ctk = types.ModuleType("ctk")
    ctk.ctkPathLineEdit = ctkPathLineEdit
    ctk.ctkCollapsibleButton = ctkCollapsibleButton
    ctk.ctkSliderWidget = ctkSliderWidget

    sys.modules.setdefault("qt", qt)
    sys.modules.setdefault("ctk", ctk)
    slicer = sys.modules.setdefault("slicer", types.ModuleType("slicer"))
    # Set unconditionally: `setdefault` above does nothing when a previous
    # install() already registered the module, and a stub missing the node
    # picker fails only in the module that happens to use one.
    slicer.qMRMLNodeComboBox = qMRMLNodeComboBox
    if not hasattr(slicer, "mrmlScene"):
        slicer.mrmlScene = None
    return qt, ctk
