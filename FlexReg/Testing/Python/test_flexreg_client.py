"""FlexReg's panel: the pads come from the schema, the preview is wired to them.

Run outside Slicer against the qt/ctk/slicer stubs, so what is asserted is which
widgets a schema produces and what the module does with them, not Qt itself.

    python3 -m unittest test_flexreg_client
"""

import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "ServerToolsCore"))
sys.path.insert(0, os.path.join(ROOT, "ServerToolsCore", "Testing", "Python"))

import qt_stubs  # noqa: E402

qt_stubs.install()

import qt  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
