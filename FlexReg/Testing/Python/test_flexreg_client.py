"""FlexReg's panel: the pads come from the schema, the preview is wired to them.

Run outside Slicer against the qt/ctk/slicer stubs, so what is asserted is which
widgets a schema produces and what the module does with them, not Qt itself.

    python3 -m unittest test_flexreg_client
"""

import contextlib
import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "ServerToolsCore"))
sys.path.insert(0, os.path.join(ROOT, "ServerToolsCore", "Testing", "Python"))

import qt_stubs  # noqa: E402

qt_stubs.install()

import qt  # noqa: E402
import slicer  # noqa: E402
from ServerToolsCoreLib import formgen  # noqa: E402


def _stub_slicer_module_framework():
    """The three `slicer` submodules AREG.py touches at import time."""
    slicer = sys.modules["slicer"]

    i18n = types.ModuleType("slicer.i18n")
    i18n.tr = lambda text: text
    sys.modules["slicer.i18n"] = i18n
    slicer.i18n = i18n

    framework = types.ModuleType("slicer.ScriptedLoadableModule")

    class ScriptedLoadableModule:
        def __init__(self, parent):
            self.parent = parent

    class ScriptedLoadableModuleWidget:
        def __init__(self, parent=None):
            pass

    framework.ScriptedLoadableModule = ScriptedLoadableModule
    framework.ScriptedLoadableModuleWidget = ScriptedLoadableModuleWidget
    sys.modules["slicer.ScriptedLoadableModule"] = framework
    slicer.ScriptedLoadableModule = framework

    util = types.ModuleType("slicer.util")

    class VTKObservationMixin:
        def __init__(self, *args, **kwargs):
            pass

    util.VTKObservationMixin = VTKObservationMixin
    # What the panel says when a run comes back with nothing to show, and how
    # it finds what a previous run left in the third view.
    util.showStatusMessage = lambda *_args: None
    util.errorDisplay = lambda *_args, **_kwargs: None
    util.infoDisplay = lambda *_args, **_kwargs: None
    util.getNodesByClass = lambda _class: []
    sys.modules["slicer.util"] = util
    slicer.util = util


_stub_slicer_module_framework()


def _schema():
    """FlexReg's arguments, as the server publishes them.

    Written out rather than fetched: the point is that this panel is built from
    a schema, and a test that needs a running server proves nothing about the
    panel.
    """
    corner = {
        "type": "vec2", "types": ["vec2"], "required": False, "choices": None,
        "server_selectable": None, "description": "", "initial": None,
        "ui": "joystick", "x_range": [0.0, 1.0], "y_range": [-5.0, 5.0],
        "x_labels": ["mid", "out"], "y_labels": ["POST", "ANT"],
    }
    arguments = {name: dict(corner) for name in (
        "anterior_right", "anterior_left", "posterior_right", "posterior_left")}
    arguments["shift"] = dict(corner, x_range=[-15.0, 15.0], y_range=[-15.0, 15.0],
                              x_labels=["L", "R"])
    return arguments


class PadsComeFromTheSchemaTest(unittest.TestCase):
    def setUp(self):
        self.widgets = formgen.build(_schema(), qt.QFormLayout())

    def test_every_corner_and_the_translation_get_a_pad(self):
        """Five pads, and no code in FlexReg.py builds any of them."""
        pads = [name for name, widget in self.widgets.items()
                if type(widget).__name__ == "JoystickInput"]

        self.assertEqual(sorted(pads), sorted(
            ["anterior_right", "anterior_left", "posterior_right",
             "posterior_left", "shift"]))

    def test_a_corner_pad_carries_the_arch_s_own_axes(self):
        """0 is mid-arch and 1 lands on the tooth, which is why both ends are
        named: "0.8" says nothing about where that is in a mouth."""
        pad = self.widgets["anterior_right"].pad

        self.assertEqual((pad.x_start, pad.x_end), (0.0, 1.0))
        self.assertEqual((pad.y_start, pad.y_end), (-5.0, 5.0))

    def test_the_translation_pad_is_millimetres_on_both_axes(self):
        pad = self.widgets["shift"].pad

        self.assertEqual((pad.x_start, pad.x_end), (-15.0, 15.0))
        self.assertEqual((pad.y_start, pad.y_end), (-15.0, 15.0))

    def test_a_pair_reads_back_as_two_numbers(self):
        """What travels to the server is the pair, in (ratio, adjust) order."""
        widget = self.widgets["anterior_right"]
        widget.xBox.setValue(0.8)
        widget.yBox.setValue(-2.0)

        self.assertEqual([round(v, 3) for v in widget.value()], [0.8, -2.0])


class PreviewWiringTest(unittest.TestCase):
    """The module reads the pads the way the preview expects them."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "FlexReg"))
        from FlexReg import CORNERS, TEETH

        self.CORNERS = CORNERS
        self.TEETH = TEETH

    @staticmethod
    def _preview_signs():
        """`ADJUST_SIGN`, or None where numpy is absent.

        The preview needs numpy, vtk and matplotlib, which Slicer ships and a
        bare CI runner may not. Skipping is honest here: this asserts that two
        name lists agree, and it runs wherever the library can be imported.
        """
        try:
            from FlexRegLib.butterfly_preview import ADJUST_SIGN
        except ImportError:
            return None
        return ADJUST_SIGN

    def test_every_corner_has_a_tooth_argument(self):
        """A corner is placed along a tooth, so the two lists cannot drift."""
        self.assertEqual(sorted(self.TEETH), sorted(self.CORNERS))
        for corner, argument in self.TEETH.items():
            self.assertEqual(argument, "tooth_" + corner)

    def test_the_corner_names_are_the_ones_the_preview_uses(self):
        """The preview keys its centroids by these exact strings; a rename on
        either side would place a patch from the wrong four teeth."""
        signs = self._preview_signs()
        if signs is None:
            self.skipTest("the preview's libraries are not installed here")

        self.assertEqual(sorted(self.CORNERS), sorted(signs))


# ---------------------------------------------------------------------------
# The pair, the three views, and what each button keeps


PATH_ARGUMENT = {
    "type": "path", "types": ["path"], "required": True, "choices": None,
    "server_selectable": None, "description": "", "initial": None,
    "extensions": None, "visible_when": None, "ui": None, "groups": None,
}
BUTTERFLY_ONLY = {"patch": "Palate (butterfly)"}


def _flexreg_schema():
    """FlexReg as `GET /tools` publishes it, in declaration order.

    Written out rather than fetched for the same reason the pads are: a test
    that needs a running server proves nothing about the panel.
    """
    arguments = {
        "surfaces": dict(PATH_ARGUMENT, label="Arches", section="Inputs"),
        "mode": {
            "type": "choice", "types": ["choice"], "required": False,
            "choices": {"Patch": False, "Register": False, "Patch and register": True},
            "server_selectable": None, "description": "", "initial": None,
            "extensions": None, "label": "What to do", "section": "Inputs",
            "visible_when": None, "ui": None, "groups": None,
        },
        "patch": {
            "type": "choice", "types": ["choice"], "required": False,
            "choices": {"Palate (butterfly)": True, "Mucogingival line": False},
            "server_selectable": None, "description": "", "initial": None,
            "extensions": None, "label": "Register on", "section": "Inputs",
            "visible_when": None, "ui": None, "groups": None,
        },
        "reference": dict(PATH_ARGUMENT, required=False,
                          label="Register onto", section="Inputs"),
    }
    # The four teeth are declared and never rendered: a clinician never changes
    # them, and the panel still reads their defaults for the preview.
    teeth = {"anterior_right": 6, "anterior_left": 11,
             "posterior_right": 3, "posterior_left": 14}
    for corner, tooth in teeth.items():
        arguments["tooth_" + corner] = {
            "type": "int", "types": ["int"], "required": False, "choices": None,
            "server_selectable": None, "description": "", "initial": tooth,
            "extensions": None, "hidden": True, "visible_when": None,
            "ui": None, "groups": None,
        }
        arguments[corner] = dict(
            _schema()[corner], section="Patch", section_columns=2,
            visible_when=dict(BUTTERFLY_ONLY), cell=corner[0] + corner.split("_")[1][0],
            label=corner.replace("_", " ").capitalize(),
        )
    arguments["shift"] = dict(_schema()["shift"], section="Patch",
                              visible_when=dict(BUTTERFLY_ONLY),
                              label="Move the whole patch")
    arguments["output_suffix"] = {
        "type": "str", "types": ["str"], "required": False, "choices": None,
        "server_selectable": None, "description": "", "initial": "_Reg",
        "extensions": None, "label": "Suffix", "section": "Outputs",
        "visible_when": None, "ui": None, "groups": None,
    }
    return {"name": "FlexReg", "output_kind": "files", "arguments": arguments}


class _FakeClient:
    def __init__(self, schema):
        self._schema = schema

    def get_tool_schema(self, _name, force_refresh=False):
        return self._schema

    def list_tool_data(self, _name):
        return {"models": [], "testfiles": []}


class _FakeDisplay:
    def __init__(self):
        self.viewNodeIDs = []
        self.activeScalar = ""
        self.scalarVisibility = False
        self.color = None

    def SetViewNodeIDs(self, ids):
        self.viewNodeIDs = list(ids)

    def GetViewNodeIDs(self):
        return list(self.viewNodeIDs)

    def GetActiveScalarName(self):
        return self.activeScalar

    def SetActiveScalarName(self, name):
        self.activeScalar = name

    def GetScalarVisibility(self):
        return self.scalarVisibility

    def SetScalarVisibility(self, value):
        self.scalarVisibility = bool(value)

    def SetScalarRangeFlag(self, _flag):
        pass

    def SetScalarRange(self, _low, _high):
        pass

    def SetColor(self, *_rgb):
        self.color = _rgb

    def SetLineWidth(self, _width):
        pass

    def SetLighting(self, _on):
        pass


class _FakeNode:
    def __init__(self, name, polydata="polydata"):
        self._name = name
        self._polydata = polydata
        self._display = _FakeDisplay()
        self.saveWithScene = True

    def GetID(self):
        return "vtkMRMLModelNode" + self._name

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name

    def GetScene(self):
        return object()

    def GetPolyData(self):
        return self._polydata

    def SetAndObservePolyData(self, polydata):
        self._polydata = polydata

    def GetDisplayNode(self):
        return self._display

    def CreateDefaultDisplayNodes(self):
        pass

    def SetSaveWithScene(self, value):
        self.saveWithScene = value

    def Modified(self):
        pass


class _FakeView:
    def __init__(self, tag):
        self.tag = tag

    def GetID(self):
        return "vtkMRMLViewNode" + self.tag


class _FakeScene:
    """The three singleton view nodes, and nothing else."""

    def __init__(self):
        self.views = {tag: _FakeView(tag) for tag in ("1", "2", "3")}
        self.added = []
        self.removed = []

    def GetSingletonNode(self, tag, _class):
        return self.views.get(tag)

    def AddNewNodeByClass(self, _class, name):
        node = _FakeNode(name)
        self.added.append(node)
        return node

    def RemoveNode(self, node):
        self.removed.append(node)

    def GetNodeByID(self, _id):
        return None


class _FakeThreeDView:
    def __init__(self):
        self.reset = 0

    def resetFocalPoint(self):
        self.reset += 1


class _FakeThreeDWidget:
    def __init__(self, view):
        self._view = view
        self.threeD = _FakeThreeDView()

    def mrmlViewNode(self):
        return self._view

    def threeDView(self):
        return self.threeD


class _FakeLayoutManager:
    """The three 3D widgets of the custom layout, in its order."""

    def __init__(self, scene):
        self.widgets = [_FakeThreeDWidget(scene.views[tag]) for tag in ("1", "2", "3")]
        self.layout = 0
        self.threeDViewCount = len(self.widgets)

    def threeDWidget(self, index):
        return self.widgets[index]

    def setLayout(self, layout):
        self.layout = layout

    def framed(self, tag):
        return self.widgets[int(tag) - 1].threeD.reset


class _FakePreview:
    """Stands in for ButterflyPreview: it needs numpy, vtk and matplotlib,
    which Slicer ships and a bare runner does not."""

    def __init__(self):
        self.ready = False
        self.error = None
        self.prepared = None
        self.calls = []

    def clear(self):
        self.ready = False
        self.prepared = None

    def matches(self, teeth):
        return self.prepared == {key: int(value) for key, value in teeth.items()}

    def prepare(self, _polydata, teeth):
        self.prepared = {key: int(value) for key, value in teeth.items()}
        self.ready = True
        return True

    def compute(self, ratios, adjusts, shift, with_fill=True):
        self.calls.append({"ratios": dict(ratios), "adjusts": dict(adjusts),
                           "shift": tuple(shift), "with_fill": with_fill})
        return ("contour", [0.0], "corners")


def _build_panel():
    """A real panel through _buildAutoUI, with the scene and the preview faked.

    __init__ is bypassed the way AREG's test bypasses it -- it reaches for
    get_client() and a live scene -- so what it would have set is set here.
    """
    sys.path.insert(0, os.path.join(ROOT, "FlexReg"))
    from FlexReg import FlexRegWidget, SIDES
    from ServerToolsCoreLib.base_widget import ServerToolWidgetBase

    # Picking an arch pins it to its view and recentres it, so the scene and
    # the layout manager have to exist before the first selection -- which in
    # Slicer they always do.
    slicer.mrmlScene = _FakeScene()
    manager = _FakeLayoutManager(slicer.mrmlScene)
    slicer.app = types.SimpleNamespace(layoutManager=lambda: manager)

    panel = ServerToolWidgetBase.__new__(FlexRegWidget)
    panel.client = _FakeClient(_flexreg_schema())
    panel._argWidgets = {}
    panel._inputWidgets = {}
    panel._inputModes = {}
    panel._sectionBoxes = {}
    panel._sectionLayouts = {}
    panel._rows = {}
    panel._rowSections = {}
    panel._sectionsWithOwnRows = set()
    panel._hiddenArgs = set()
    panel._schema = None
    panel._schemaError = None
    panel._outputFolderWidget = None
    panel.applyButton = None
    panel.cancelButton = None
    # What a job leaves behind when it ends, however it ends.
    panel._job = None
    panel._workspace = None
    panel._elapsedTimer = None
    panel._jobStartedAt = None
    panel._jobPhase = ""
    panel._progressLabel = None

    panel._previews = {side: _FakePreview() for side in SIDES}
    panel._previewSurfaces = {side: None for side in SIDES}
    panel._contourNodes = {}
    panel._paintedNodes = {}
    panel._previousLayout = None
    panel._previewCheckBox = None
    panel._previewStatus = None
    panel._seeButton = None
    panel._previewDir = None

    panel._buildAutoUI(qt.QVBoxLayout())
    # setup() calls this after the form, and the preview switch, the status
    # line and the See button are all built in it.
    panel.addExtraWidgets(qt.QVBoxLayout())

    # Painting an arch is the one leaf that needs numpy and vtk. Recorded
    # instead, so everything above it -- which is what these tests are about --
    # runs on a machine that has neither.
    panel.painted = []
    panel._paint = lambda node, labels, name: panel.painted.append((node, name))
    return panel


def _pick(panel, side, node):
    panel._inputWidgets[side].setCurrentNode(node)


class ArchesAreSceneNodesTest(unittest.TestCase):
    """T1 and T2 have to be IN the scene to be shown and shaped. A path cannot
    be drawn on, which is why the panel does not offer the tool's folder form."""

    def setUp(self):
        self.panel = _build_panel()

    def test_both_timepoints_are_picked_out_of_the_scene(self):
        from FlexReg import SIDES

        for side in SIDES:
            self.assertEqual(self.panel._inputModes[side], "model_node")
            self.assertEqual(
                self.panel._inputWidgets[side].nodeTypes, ["vtkMRMLModelNode"]
            )

    def test_the_teeth_are_declared_hidden_and_still_read_for_the_preview(self):
        """They are not sent (the server applies the same defaults), but the
        preview is built from them here: a panel reading 0 would place the
        patch on a tooth no arch has."""
        self.assertIn("tooth_anterior_right", self.panel._hiddenArgs)
        self.assertEqual(
            self.panel._selectedTeeth(),
            {"anterior_right": 6, "anterior_left": 11,
             "posterior_right": 3, "posterior_left": 14},
        )


class WhatEachButtonWaitsForTest(unittest.TestCase):
    def setUp(self):
        self.panel = _build_panel()
        self.panel.applyButton = qt.QPushButton("Apply")

    def test_apply_waits_for_both_arches(self):
        self.assertFalse(self.panel._inputReady())
        _pick(self.panel, "reference", _FakeNode("T1"))
        self.assertFalse(self.panel._inputReady())
        _pick(self.panel, "surfaces", _FakeNode("T2"))
        self.assertTrue(self.panel._inputReady())

    def test_see_does_not_wait_for_an_output_folder(self):
        """Apply does -- it writes files. See writes none, and asking for a
        folder before anyone has decided the patch is right is the wrong order.
        """
        _pick(self.panel, "reference", _FakeNode("T1"))
        _pick(self.panel, "surfaces", _FakeNode("T2"))
        self.panel._checkCanApply()

        self.assertEqual(self.panel._outputFolderWidget.currentPath, "")
        self.assertFalse(self.panel.applyButton.enabled)
        self.assertTrue(self.panel._seeButton.enabled)


class ThreeViewsTest(unittest.TestCase):
    """T1 left, T2 middle, the registration right -- and the third view empty
    until one comes back."""

    def setUp(self):
        self.panel = _build_panel()
        # The scene _build_panel installed, not a new one: the layout manager's
        # widgets hold ITS view nodes, and _frame matches them by identity.
        self.scene = slicer.mrmlScene
        self.t1 = _FakeNode("T1")
        self.t2 = _FakeNode("T2")
        _pick(self.panel, "reference", self.t1)
        _pick(self.panel, "surfaces", self.t2)

    def test_each_arch_is_shown_in_its_own_view_only(self):
        """An empty view list means EVERY view, which is one arch too many in
        each window."""
        self.panel._pinArches()

        self.assertEqual(self.t1.GetDisplayNode().viewNodeIDs, ["vtkMRMLViewNode1"])
        self.assertEqual(self.t2.GetDisplayNode().viewNodeIDs, ["vtkMRMLViewNode2"])

    def test_the_third_view_holds_nothing_until_a_run_comes_back(self):
        self.panel._pinArches()

        for node in (self.t1, self.t2):
            self.assertNotIn("vtkMRMLViewNode3", node.GetDisplayNode().viewNodeIDs)

    def test_each_view_is_recentred_on_the_arch_it_was_just_given(self):
        """Two acquisitions a year apart are not at the same place in world
        space, and a view opens on the scene's bounds rather than on the one
        arch it was restricted to: without this an arch lands beside its own
        window, or outside it."""
        manager = slicer.app.layoutManager()
        before = [manager.framed(tag) for tag in ("1", "2", "3")]

        self.panel._onArchChanged()

        after = [manager.framed(tag) for tag in ("1", "2", "3")]
        self.assertEqual(after[0], before[0] + 1)
        self.assertEqual(after[1], before[1] + 1)
        # Nothing has been registered yet, so there is nothing to frame there.
        self.assertEqual(after[2], before[2])

    def test_a_registered_arch_lands_in_the_third_view_beside_the_reference(self):
        """Judging a registration means seeing the two surfaces together, so
        the reference is ADDED to that view rather than moved into it."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        open(os.path.join(directory, "T2_Reg.vtk"), "w").close()

        loaded = _FakeNode("loaded")
        with _loading(loaded):
            self.panel._loadRegistered(directory)

        self.assertEqual(loaded.GetDisplayNode().viewNodeIDs, ["vtkMRMLViewNode3"])
        self.assertEqual(self.t1.GetDisplayNode().viewNodeIDs,
                         ["vtkMRMLViewNode1", "vtkMRMLViewNode3"])

    def test_the_patched_reference_is_not_loaded_a_second_time(self):
        """It is already in the scene as the user's own node; a second copy in
        the same view draws every triangle twice and looks like a perfect
        registration."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        open(os.path.join(directory, "T1_patch.vtk"), "w").close()

        loaded = _FakeNode("loaded")
        with _loading(loaded) as calls:
            self.panel._loadRegistered(directory)

        self.assertEqual(calls, [])


class PreviewCoversBothArchesTest(unittest.TestCase):
    """One set of pads, two arches. The patch is the same region of the same
    mouth a year apart, and it has to be on both meshes: the ICP reads it off
    the moving surface AND the reference."""

    def setUp(self):
        self.panel = _build_panel()
        _pick(self.panel, "reference", _FakeNode("T1"))
        _pick(self.panel, "surfaces", _FakeNode("T2"))
        for preview in self.panel._previews.values():
            preview.calls = []
        self.panel.painted = []

    def test_one_gesture_redraws_both_arches(self):
        self.panel._onPatchChanged()

        for side, preview in self.panel._previews.items():
            self.assertEqual(len(preview.calls), 1, side)
        self.assertEqual(len(self.panel.painted), 2)

    def test_the_fill_is_drawn_not_only_the_outline(self):
        """The outline says where the boundary is; on a curved palate it does
        not say which side of it you are on."""
        self.panel._onPatchChanged()

        for preview in self.panel._previews.values():
            self.assertTrue(preview.calls[0]["with_fill"])

    def test_both_arches_get_the_same_pad_values(self):
        self.panel._argWidgets["anterior_right"].xBox.setValue(0.8)
        self.panel._onPatchChanged()

        seen = [preview.calls[-1]["ratios"]["anterior_right"]
                for preview in self.panel._previews.values()]
        self.assertEqual([round(value, 3) for value in seen], [0.8, 0.8])


class SeeKeepsNothingTest(unittest.TestCase):
    def setUp(self):
        self.panel = _build_panel()

    def test_the_run_writes_into_a_directory_this_module_owns(self):
        """Not the request's workspace: _teardownJob deletes that BEFORE
        handleResult, so a run that reads its own output afterwards has to own
        the directory it wrote to."""
        self.panel._previewDir = "/tmp/flexreg-see"

        self.assertEqual(self.panel.outputDirectory(_FakeWorkspace("/tmp/ws")),
                         "/tmp/flexreg-see")

    def test_a_failed_run_does_not_capture_the_next_apply(self):
        """The next press of Apply reads outputDirectory() too: a preview
        directory left set would send the user's results into a temporary
        folder about to be deleted."""
        directory = tempfile.mkdtemp()
        self.panel._previewDir = directory
        self.panel.applyButton = qt.QPushButton("Apply")
        self.panel.cancelButton = qt.QPushButton("Cancel")

        self.panel._onJobError(RuntimeError("the server said no"))

        self.assertIsNone(self.panel._previewDir)
        self.assertFalse(os.path.exists(directory))

    def test_nothing_is_left_behind_once_the_result_is_in_the_scene(self):
        directory = tempfile.mkdtemp()
        self.panel._previewDir = directory
        open(os.path.join(directory, "T2_Reg.vtk"), "w").close()

        with _loading(_FakeNode("loaded")):
            self.panel.handleResult(_FakeResult(os.path.join(directory, "T2_Reg.vtk")))

        self.assertFalse(os.path.exists(directory))
        self.assertIsNone(self.panel._previewDir)


class _FakeWorkspace:
    def __init__(self, path):
        self.path = path


class _FakeResult:
    def __init__(self, path):
        self.path = path


@contextlib.contextmanager
def _loading(node):
    """Answer `slicer_io.load_result` with a node instead of reading a file."""
    from ServerToolsCoreLib import slicer_io

    calls = []
    original = slicer_io.load_result

    def _load(path, kind):
        calls.append((path, kind))
        return node

    slicer_io.load_result = _load
    try:
        yield calls
    finally:
        slicer_io.load_result = original


if __name__ == "__main__":
    unittest.main()
