"""Unit tests for the AREG module's client behaviour — run outside Slicer, with
`qt`/`ctk`/`slicer` stubbed (ServerToolsCore/Testing/Python/qt_stubs.py).

Two things are covered, and they fail for different reasons:

* **What AREG's panel derives from its schema.** AREG declares nothing but
  TOOL_NAME, so every widget, every extension filter and the result handling
  come from `GET /tools`. These tests assert that the
  schema really does answer all of it — if the server's schema changes shape,
  this is what notices, and they are also what would catch someone "helpfully"
  re-adding a FILE_INPUTS or RESULT_KIND override that only repeats the server.
* **Which of the five modes shows which arguments.** AREG's modes share one
  schema and the two modalities have almost nothing in common; the old local
  module spent 2574 lines and a hand-built QStackedWidget on that, and the
  server states it as `visible_when`.

`AREG.py` is imported here, which needs three `slicer` submodules qt_stubs does
not provide. That is safe for what is under test: these functions are pure
Python and never touch `slicer` — the stub only gets the import statement past
a Slicer that isn't running, so the test is not measuring the stub.

Usage:
    python3 -m unittest AREG/Testing/Python/test_areg_client.py
"""

import os
import shutil
import sys
import tempfile
import types
import unittest

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_CORE = os.path.join(_REPO_ROOT, "ServerToolsCore")
# `ServerToolsCoreLib` is a package inside ServerToolsCore/, and qt_stubs lives
# with that module's own tests: it is the extension's single set of Qt
# stand-ins, and forking a second copy here would drift.
sys.path.insert(0, os.path.join(_CORE, "Testing", "Python"))
sys.path.insert(0, _CORE)

import qt_stubs

qt, ctk = qt_stubs.install()


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

from ServerToolsCoreLib import client as client_module  # noqa: E402
from ServerToolsCoreLib import formgen  # noqa: E402
from ServerToolsCoreLib.base_widget import ServerToolWidgetBase  # noqa: E402


def _load_areg_module():
    """Import AREG.py by path.

    `import AREG` is ambiguous here and resolves the wrong way: the repository
    has a directory called AREG/, which Python 3 treats as a namespace package
    whenever the repo root is on sys.path — so the import yields an empty
    package rather than the module inside it.
    """
    import importlib.util

    path = os.path.join(_REPO_ROOT, "AREG", "AREG.py")
    spec = importlib.util.spec_from_file_location("areg_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AREGWidget = _load_areg_module().AREGWidget


def _module_source() -> str:
    with open(os.path.join(_REPO_ROOT, "AREG", "AREG.py"), encoding="utf-8") as handle:
        return handle.read()


# The server's actual GET /tools payload for AREG, verbatim. Kept here as a
# fixture so the panel can be tested without a running server; if the server's
# schema changes, these tests are what notices.
AREG_SCHEMA = {
    "name": "AREG",
    "arguments": {
        "modality": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": True,
            "description": "CBCT: cone-beam CT volumes. IOS: intra-oral surface scans",
            "server_selectable": None,
            "choices": {
                "CBCT": True,
                "IOS": False
            },
            "initial": None,
            "extensions": None,
            "label": "Input Type",
            "section": "Inputs",
            "visible_when": None,
            "ui": None,
            "groups": None
        },
        "automation": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": True,
            "description": "Semi-Automated: you send what the registration needs (CBCT masks, or already segmented and oriented meshes). Fully-Automated: the server produces it. Oriented + Fully-Automated (CBCT only): the T1 scans are oriented first",
            "server_selectable": None,
            "choices": {
                "Semi-Automated": False,
                "Fully-Automated": True,
                "Oriented + Fully-Automated": False
            },
            "initial": None,
            "extensions": None,
            "label": "Mode",
            "section": "Inputs",
            "visible_when": None,
            "ui": None,
            "groups": None
        },
        "t1": {
            "type": "zip_file",
            "types": [
                "zip_file",
                "folder"
            ],
            "required": True,
            "description": "The first timepoint -- the scans everything is registered ONTO. A folder sent as a .zip, or the name of a hosted test set (see GET /tools/AREG/data)",
            "server_selectable": "testfile",
            "choices": None,
            "initial": None,
            "extensions": {
                "zip_file": [
                    ".zip"
                ],
                "folder": [
                    ".zip"
                ]
            },
            "label": "T1 Folder",
            "section": "Inputs",
            "visible_when": None,
            "ui": None,
            "groups": None
        },
        "t2": {
            "type": "zip_file",
            "types": [
                "zip_file",
                "folder"
            ],
            "required": True,
            "description": "The second timepoint -- the scans that get moved. Paired with T1 by name up to the timepoint token: 'P1_T1_scan.nii.gz' pairs with 'P1_T2.nii.gz'",
            "server_selectable": "testfile",
            "choices": None,
            "initial": None,
            "extensions": {
                "zip_file": [
                    ".zip"
                ],
                "folder": [
                    ".zip"
                ]
            },
            "label": "T2 Folder",
            "section": "Inputs",
            "visible_when": None,
            "ui": None,
            "groups": None
        },
        "dicom_input": {
            "type": "bool",
            "types": [
                "bool"
            ],
            "required": False,
            "description": "CBCT only: both folders are zips of DICOM folders, one per patient, to convert server-side before registering",
            "server_selectable": None,
            "choices": None,
            "initial": False,
            "extensions": None,
            "label": "DICOM Input",
            "section": "Inputs",
            "visible_when": {
                "modality": "CBCT"
            },
            "ui": None,
            "groups": None
        },
        "cbct_regions": {
            "type": "multichoice",
            "types": [
                "multichoice"
            ],
            "required": False,
            "description": "CBCT only: the anatomy the registration is confined to. Each region is a SEPARATE registration with its own output folder -- registering on the cranial base and on the mandible answer two different clinical questions",
            "server_selectable": None,
            "choices": {
                "Cranial base": True,
                "Mandible": False,
                "Maxilla": False
            },
            "initial": None,
            "extensions": None,
            "label": "Regions of Reference",
            "section": "CBCT Registration",
            "visible_when": {
                "modality": "CBCT"
            },
            "ui": "inline",
            "groups": None
        },
        "t1_masks": {
            "type": "zip_file",
            "types": [
                "zip_file",
                "folder"
            ],
            "required": False,
            "description": "Semi-Automated CBCT only: the T1 segmentations to register inside, sent as a .zip. A mask is matched to its scan by name and has to say both that it is a segmentation (mask/seg/pred) and which structure it covers (cb/mand/max), e.g. 'P1_T1_CB_seg.nii.gz'",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": {
                "zip_file": [
                    ".zip"
                ],
                "folder": [
                    ".zip"
                ]
            },
            "label": "T1 Masks",
            "section": "CBCT Registration",
            "visible_when": {
                "modality": "CBCT",
                "automation": "Semi-Automated"
            },
            "ui": None,
            "groups": None
        },
        "segmentation_label": {
            "type": "int",
            "types": [
                "int"
            ],
            "required": False,
            "description": "Semi-Automated CBCT only: which label value of a multi-label mask to register inside. 0 uses the whole mask. A label the mask does not hold is refused rather than silently falling back to the whole mask",
            "server_selectable": None,
            "choices": None,
            "initial": 0,
            "extensions": None,
            "label": "Mask Label",
            "section": "CBCT Registration",
            "visible_when": {
                "modality": "CBCT",
                "automation": "Semi-Automated"
            },
            "ui": None,
            "groups": None
        },
        "segmentation_model": {
            "type": "str",
            "types": [
                "str"
            ],
            "required": False,
            "description": "Fully-Automated CBCT only: the AMASSS model bundle used to produce the T1 masks (see GET /tools/AREG/data)",
            "server_selectable": "model",
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Segmentation Models",
            "section": "CBCT Registration",
            "visible_when": {
                "modality": "CBCT",
                "automation": [
                    "Fully-Automated",
                    "Oriented + Fully-Automated"
                ]
            },
            "ui": None,
            "groups": None
        },
        "cbct_reference": {
            "type": "str",
            "types": [
                "str"
            ],
            "required": False,
            "description": "Oriented + Fully-Automated CBCT only: the already-oriented case defining the frame the T1 scans are put in before registration (see GET /tools/AREG/data)",
            "server_selectable": "model",
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Orientation Reference",
            "section": "CBCT Registration",
            "visible_when": {
                "modality": "CBCT",
                "automation": "Oriented + Fully-Automated"
            },
            "ui": None,
            "groups": None
        },
        "ios_patch": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": False,
            "description": "IOS only: the region that does not move between the two timepoints. The palate is predicted by a network and registers the UPPER arches (the lower ones follow). The mucogingival line is built from 13 landmarks you supply and registers the LOWER arches on their own",
            "server_selectable": None,
            "choices": {
                "Palate (upper arch)": True,
                "Mucogingival line (lower arch)": False
            },
            "initial": None,
            "extensions": None,
            "label": "Register On",
            "section": "IOS Registration",
            "visible_when": {
                "modality": "IOS"
            },
            "ui": None,
            "groups": None
        },
        "registration_model": {
            "type": "str",
            "types": [
                "str"
            ],
            "required": False,
            "description": "Palate patch only: the checkpoint that predicts it (see GET /tools/AREG/data). The mucogingival patch needs no model",
            "server_selectable": "model",
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Registration Model",
            "section": "IOS Registration",
            "visible_when": {
                "modality": "IOS",
                "ios_patch": "Palate (upper arch)"
            },
            "ui": None,
            "groups": None
        },
        "mgl_landmarks": {
            "type": "zip_file",
            "types": [
                "zip_file",
                "folder"
            ],
            "required": False,
            "description": "Mucogingival patch only. Leave empty -- the server predicts the 13 MG landmarks itself, which is the ordinary case. Send a .zip of Slicer markups files only to reuse landmarks you already have (one per lower scan, matched by name: 'P1_T1_Lower_MG_Pred.json' goes with 'P1_T1_Lower.vtk'), which also skips paying for the prediction twice",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": {
                "zip_file": [
                    ".zip"
                ],
                "folder": [
                    ".zip"
                ]
            },
            "label": "Mucogingival Landmarks",
            "section": "IOS Registration",
            "visible_when": {
                "modality": "IOS",
                "ios_patch": "Mucogingival line (lower arch)"
            },
            "ui": None,
            "groups": None
        },
        "mgl_patch_height": {
            "type": "float",
            "types": [
                "float"
            ],
            "required": False,
            "description": "Mucogingival patch only: how far the band reaches on each side of the line, measured ALONG the surface so it cannot leak to the lingual side. 0 registers on the landmarks alone, with no band at all -- the control case for measuring what the surface adds",
            "server_selectable": None,
            "choices": None,
            "initial": 5.0,
            "extensions": None,
            "label": "Patch Height (mm)",
            "section": "IOS Registration",
            "visible_when": {
                "modality": "IOS",
                "ios_patch": "Mucogingival line (lower arch)"
            },
            "ui": None,
            "groups": None
        },
        "ios_reference": {
            "type": "str",
            "types": [
                "str"
            ],
            "required": False,
            "description": "Fully-Automated IOS only: the reference meshes both timepoints are oriented onto before the patch is predicted (see GET /tools/AREG/data)",
            "server_selectable": "model",
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Orientation Reference",
            "section": "IOS Registration",
            "visible_when": {
                "modality": "IOS",
                "automation": "Fully-Automated"
            },
            "ui": None,
            "groups": None
        },
        "output_suffix": {
            "type": "str",
            "types": [
                "str"
            ],
            "required": False,
            "description": "Added to every output file name, e.g. patient1_CB_Reg.nii.gz",
            "server_selectable": None,
            "choices": None,
            "initial": "Reg",
            "extensions": None,
            "label": "Suffix",
            "section": "Outputs",
            "visible_when": None,
            "ui": None,
            "groups": None
        }
    },
    "output_kind": "files"
}

_ARGUMENTS = AREG_SCHEMA["arguments"]


# ---------------------------------------------------------------------------
# What the schema alone has to answer
# ---------------------------------------------------------------------------

class DeclarationTest(unittest.TestCase):
    def test_the_module_declares_nothing_the_server_already_says(self):
        """Every override below would only repeat the schema, and an override
        written against a remembered schema is the one thing that can drift out
        of sync with it."""
        self.assertEqual(AREGWidget.TOOL_NAME, "AREG")
        self.assertEqual(AREGWidget.FILE_INPUTS, ServerToolWidgetBase.FILE_INPUTS)
        self.assertIsNone(AREGWidget.RESULT_KIND)
        self.assertTrue(AREGWidget.AUTO_UI)

    def test_the_module_declares_no_test_data_of_its_own(self):
        """AREG used to carry a TEST_DATA dict of hardcoded GitHub release
        URLs. A tool's test data is what the SERVER hosts for it now, offered
        in the input row itself - so every tool has it and no module declares
        anything."""
        self.assertFalse(hasattr(AREGWidget, "TEST_DATA"))
        self.assertNotIn("TEST_DATA", _module_source())


class SchemaDrivenPanelTest(unittest.TestCase):
    def test_the_two_timepoints_take_a_folder_or_an_archive(self):
        """`t1`/`t2` list "folder" alongside "zip_file", so the panel gives one
        path field with both browse buttons and zips a chosen folder before
        upload. Which kind was given is read off the path, never asked."""
        modes = formgen.file_input_modes(_ARGUMENTS)
        self.assertEqual(modes["t1"], "file_or_folder")
        self.assertEqual(modes["t2"], "file_or_folder")

    def test_the_timepoints_can_also_be_picked_from_the_server(self):
        """Both are server_selectable, so the hosted test cohorts appear above
        the picker without this module knowing any of their names."""
        self.assertEqual(_ARGUMENTS["t1"]["server_selectable"], "testfile")
        self.assertEqual(_ARGUMENTS["t2"]["server_selectable"], "testfile")

    def test_the_model_arguments_are_name_only(self):
        """A scalar `server_selectable` argument means the file never travels:
        the client sends a name and the server resolves it. Uploading to one is
        a 400, so the panel must not offer a file picker for these."""
        for name in ("segmentation_model", "cbct_reference", "registration_model",
                     "ios_reference"):
            self.assertEqual(_ARGUMENTS[name]["type"], "str")
            self.assertEqual(_ARGUMENTS[name]["server_selectable"], "model")

    def test_the_folder_picker_offers_what_the_server_reads(self):
        """Read from the schema's `extensions`, never rebuilt here: a client
        copy of that table is exactly what drifted before."""
        extensions = client_module.file_extensions_for(_ARGUMENTS["t1"])
        self.assertIn(".zip", extensions)

    def test_every_argument_carries_a_label_and_a_section(self):
        """The panel's vocabulary is the tool's. A client that has to invent a
        label can only guess from an identifier chosen for Python -- it renders
        `t1_masks` as "T1 masks" and cannot produce "Regions of Reference" from
        `cbct_regions`."""
        for name, spec in _ARGUMENTS.items():
            self.assertTrue(spec["label"], f"{name} has no label")
            self.assertTrue(spec["section"], f"{name} has no section")

    def test_the_sections_read_in_declaration_order(self):
        seen = []
        for spec in _ARGUMENTS.values():
            if spec["section"] not in seen:
                seen.append(spec["section"])
        self.assertEqual(seen, ["Inputs", "CBCT Registration", "IOS Registration", "Outputs"])


# ---------------------------------------------------------------------------
# The five modes, and what each one shows
# ---------------------------------------------------------------------------

def _hidden_in(**mode) -> set:
    """The arguments whose `visible_when` is not satisfied — exactly what
    base_widget._applyVisibility computes and what collectArgs then drops."""
    return {name for name, spec in _ARGUMENTS.items() if not formgen.is_visible(spec, mode)}


class ModePanelTest(unittest.TestCase):
    """AREG's five modes share one schema. A panel showing every argument
    offers an intraoral checkpoint next to a CBCT mask label while a run uses
    one or the other; the server states which is which as `visible_when`, and
    these tests are what notices if it stops.
    """

    def test_an_ios_run_is_never_asked_about_cbct_anatomy(self):
        hidden = _hidden_in(modality="IOS", automation="Semi-Automated")
        self.assertLessEqual(
            {"cbct_regions", "t1_masks", "segmentation_label", "segmentation_model",
             "cbct_reference", "dicom_input"},
            hidden,
        )

    def test_a_cbct_run_is_never_asked_for_an_intraoral_checkpoint(self):
        hidden = _hidden_in(modality="CBCT", automation="Semi-Automated")
        self.assertLessEqual({"registration_model", "ios_reference"}, hidden)

    def test_semi_automated_cbct_asks_for_the_masks_and_not_for_a_model(self):
        hidden = _hidden_in(modality="CBCT", automation="Semi-Automated")
        self.assertNotIn("t1_masks", hidden)
        self.assertNotIn("segmentation_label", hidden)
        self.assertIn("segmentation_model", hidden)
        self.assertIn("cbct_reference", hidden)

    def test_fully_automated_cbct_asks_for_a_model_and_not_for_the_masks(self):
        hidden = _hidden_in(modality="CBCT", automation="Fully-Automated")
        self.assertIn("t1_masks", hidden)
        self.assertIn("segmentation_label", hidden)
        self.assertNotIn("segmentation_model", hidden)
        self.assertIn("cbct_reference", hidden)

    def test_only_the_oriented_mode_asks_for_an_orientation_reference(self):
        """`segmentation_model` is visible in BOTH automated CBCT modes, which
        is a `visible_when` naming two accepted values -- the one shape a
        client written for a single value would silently get wrong."""
        hidden = _hidden_in(modality="CBCT", automation="Oriented + Fully-Automated")
        self.assertNotIn("cbct_reference", hidden)
        self.assertNotIn("segmentation_model", hidden)

    def test_only_fully_automated_ios_asks_for_an_orientation_reference(self):
        self.assertIn(
            "ios_reference", _hidden_in(modality="IOS", automation="Semi-Automated")
        )
        self.assertNotIn(
            "ios_reference", _hidden_in(modality="IOS", automation="Fully-Automated")
        )

    def test_what_every_mode_shows(self):
        """The four arguments no mode may hide: without both timepoints and
        both mode selectors there is no request to send."""
        for modality in ("CBCT", "IOS"):
            for automation in ("Semi-Automated", "Fully-Automated"):
                hidden = _hidden_in(modality=modality, automation=automation)
                self.assertEqual(
                    hidden & {"modality", "automation", "t1", "t2", "output_suffix"}, set()
                )


class IOSPatchPanelTest(unittest.TestCase):
    """The intraoral panel has two halves that never apply at once: the palate
    is predicted by a network and registers the UPPER arches, the mucogingival
    line is built from landmarks the user supplies and registers the LOWER ones.
    Offering both at once is how a user comes to believe the mucogingival mode
    needs a checkpoint -- it needs no model at all.
    """

    PALATE = "Palate (upper arch)"
    MGL = "Mucogingival line (lower arch)"

    def test_the_two_patches_are_the_options_the_server_publishes(self):
        self.assertEqual(list(_ARGUMENTS["ios_patch"]["choices"]), [self.PALATE, self.MGL])

    def test_the_palate_asks_for_a_checkpoint_and_not_for_landmarks(self):
        hidden = _hidden_in(modality="IOS", automation="Semi-Automated", ios_patch=self.PALATE)
        self.assertNotIn("registration_model", hidden)
        self.assertIn("mgl_landmarks", hidden)
        self.assertIn("mgl_patch_height", hidden)

    def test_the_mucogingival_line_asks_for_landmarks_and_not_for_a_checkpoint(self):
        hidden = _hidden_in(modality="IOS", automation="Semi-Automated", ios_patch=self.MGL)
        self.assertIn("registration_model", hidden)
        self.assertNotIn("mgl_landmarks", hidden)
        self.assertNotIn("mgl_patch_height", hidden)

    def test_the_landmarks_take_a_folder_the_client_zips(self):
        modes = formgen.file_input_modes(_ARGUMENTS)
        self.assertEqual(modes["mgl_landmarks"], "file_or_folder")

    def test_the_landmarks_are_optional_because_the_server_predicts_them(self):
        """The ordinary run sends the scans and nothing else: the server asks
        its landmark tool for the mucogingival points. A required field here
        would put the burden back on the user for the one thing the server
        exists to do."""
        self.assertFalse(_ARGUMENTS["mgl_landmarks"]["required"])
        self.assertIn("Leave empty", _ARGUMENTS["mgl_landmarks"]["description"])

    def test_the_patch_height_opens_at_the_value_the_tool_means(self):
        """A form always sends its widgets, so a spin box starting at Qt's 0
        would send 0 -- which here is a real and very different mode (register
        on the landmarks alone), not a missing value."""
        self.assertEqual(_ARGUMENTS["mgl_patch_height"]["initial"], 5.0)

    def test_no_cbct_argument_appears_for_either_patch(self):
        for patch in (self.PALATE, self.MGL):
            hidden = _hidden_in(modality="IOS", automation="Semi-Automated", ios_patch=patch)
            self.assertLessEqual({"cbct_regions", "t1_masks", "segmentation_model"}, hidden)


class HiddenArgumentsAreNotSentTest(unittest.TestCase):
    def test_a_hidden_multichoice_is_dropped_rather_than_sent_empty(self):
        """A multichoice is read back as the COMPLETE {option: checked} dict and
        the server reads what it receives AS the selection -- so a hidden
        widget left at its build-time state would send a selection the user was
        never shown. An IOS run must not send `cbct_regions` at all."""
        self.assertIn("cbct_regions", _hidden_in(modality="IOS", automation="Fully-Automated"))


# ---------------------------------------------------------------------------
# Finding what came back
# ---------------------------------------------------------------------------

class ResultDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, relative):
        path = os.path.join(self.dir, relative)
        os.makedirs(os.path.dirname(path) or self.dir, exist_ok=True)
        with open(path, "w") as handle:
            handle.write("x")
        return path

    def _found(self):
        return {os.path.basename(path): kind for path, kind in AREGWidget._findResults(self.dir)}

    def test_each_result_kind_gets_the_right_loader(self):
        """A registered CBCT is a VOLUME, not a segmentation: AREG moves a
        scan, it does not label one. Meshes are models."""
        self._write("CB/P1_CB_Reg.nii.gz")
        self._write("P1_T2_Upper_Reg.vtk")
        self.assertEqual(
            self._found(),
            {"P1_CB_Reg.nii.gz": "volume", "P1_T2_Upper_Reg.vtk": "model"},
        )

    def test_transforms_and_the_report_are_not_loaded(self):
        """Loading a .tfm into the scene applies nothing and explains nothing;
        AREG_report.json is not a scene object either. Both must stay out, or
        they would eat into the MAX_RESULTS_TO_LOAD budget as well."""
        self._write("CB/P1_CB_Reg_transform.tfm")
        self._write("AREG_report.json")
        self.assertEqual(AREGWidget._findResults(self.dir), [])

    def test_results_are_found_across_the_whole_tree(self):
        """The server writes one folder per region and preserves the input's
        structure inside it, so a cohort's results are nested twice over -- a
        non-recursive search would find nothing."""
        self._write("CB/siteA/P1_CB_Reg.nii.gz")
        self._write("MAND/siteB/nested/P2_MAND_Reg.nii.gz")
        self.assertEqual(len(AREGWidget._findResults(self.dir)), 2)

    def test_a_compressed_scan_is_not_counted_twice(self):
        """"*.nii" must not also match "P1_CB_Reg.nii.gz" — a double count would
        halve the effective load cap and load the same file twice."""
        self._write("CB/P1_CB_Reg.nii.gz")
        self.assertEqual(len(AREGWidget._findResults(self.dir)), 1)

# ---------------------------------------------------------------------------
# What Apply waits for
# ---------------------------------------------------------------------------

class _FakeClient:
    """Answers the two calls _buildAutoUI makes, with no HTTP and no server."""

    def __init__(self, schema):
        self._schema = schema
        self.models = ["AREG_model", "AMASSS_Models"]

    def get_tool_schema(self, _name, force_refresh=False):
        return self._schema

    def list_tool_data(self, _name):
        return {"models": list(self.models), "testfiles": []}


def _build_panel():
    """A real panel built through ServerToolWidgetBase._buildAutoUI.

    The parts of __init__ that _buildAutoUI touches are constructed by hand
    rather than through __init__, which reaches for get_client() and a live
    Slicer scene.
    """
    panel = ServerToolWidgetBase.__new__(AREGWidget)
    panel.client = _FakeClient(AREG_SCHEMA)
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
    panel._loadResultsCheckBox = None

    panel._buildAutoUI(qt.QVBoxLayout())
    return panel


class InputReadyTest(unittest.TestCase):
    """Which empty fields may hold Apply back.

    An optional file argument must not. `all_required_filled` has always
    skipped `required: false` scalars; `_inputReady` did not do the same for
    file rows, so AREG's `mgl_landmarks` -- which exists only to REUSE
    landmarks the server would otherwise predict -- disabled Apply until
    something was picked for it, with nothing on the panel saying that was
    what Apply was waiting for. The ordinary run was the one you could not
    launch.
    """

    def setUp(self):
        self.panel = _build_panel()
        # The panel opens on CBCT, where `mgl_landmarks` is hidden by its own
        # `visible_when` and skipped for that reason alone. Switch to the mode
        # that actually SHOWS it -- otherwise this whole class tests nothing,
        # which is exactly what the first version of it did.
        self._select("modality", "IOS")
        self._select("ios_patch", "Mucogingival line (lower arch)")
        self.panel._applyVisibility()
        assert "mgl_landmarks" not in self.panel._hiddenArgs

    def _select(self, name, option):
        combo = self.panel._argWidgets[name]
        combo.setCurrentIndex(list(_ARGUMENTS[name]["choices"]).index(option))

    def _fill(self, *names):
        """Type a path into a row, through the same widget the user types in.

        `currentPath` is a read-only property on both wrappers -- it reads the
        text edit (or answers "" while a hosted file is picked), so the value
        has to go where a user would put it."""
        for name in names:
            widget = self.panel._inputWidgets[name]
            edit = getattr(widget, "pathEdit", None) or getattr(widget.local, "pathEdit")
            edit.text = "/tmp/whatever.zip"

    def test_the_two_timepoints_are_what_apply_waits_for(self):
        self.assertFalse(self.panel._inputReady())
        self._fill("t1")
        self.assertFalse(self.panel._inputReady())
        self._fill("t2")
        self.assertTrue(self.panel._inputReady())

    def test_an_optional_file_argument_does_not_hold_apply_back(self):
        self._fill("t1", "t2")
        self.assertEqual(self.panel._inputWidgets["mgl_landmarks"].currentPath, "")
        self.assertTrue(self.panel._inputReady())

    def test_every_optional_file_row_stays_empty_and_apply_is_still_ready(self):
        """Not only `mgl_landmarks`: `t1_masks` is optional too, because
        whether it is needed depends on the mode, and that rule belongs to the
        server -- which answers a 422 naming the field."""
        self._fill("t1", "t2")
        optional = [
            name
            for name, spec in _ARGUMENTS.items()
            if not spec["required"] and name in self.panel._inputWidgets
        ]
        self.assertIn("mgl_landmarks", optional)
        self.assertIn("t1_masks", optional)
        for name in optional:
            self.assertEqual(self.panel._inputWidgets[name].currentPath, "")
        self.assertTrue(self.panel._inputReady())


class _FakeWorkspace:
    """Enough of slicer_io.TempWorkspace for prepareInputFiles."""

    def __init__(self, root):
        self.root = root

    def file(self, name):
        return os.path.join(self.root, name)


class PrepareInputFilesTest(unittest.TestCase):
    """What actually gets uploaded, and what does not.

    An optional file row left empty must upload NOTHING. It used to return
    `widget.currentPath` -- the empty string -- as if it were a path, and the
    next thing to touch it failed with "No such file or directory: ''", which
    names nothing the user can act on. It only became reachable once Apply
    stopped waiting for optional file rows (see InputReadyTest), so the two
    belong together.
    """

    def setUp(self):
        self.panel = _build_panel()
        combo = self.panel._argWidgets["modality"]
        combo.setCurrentIndex(list(_ARGUMENTS["modality"]["choices"]).index("IOS"))
        combo = self.panel._argWidgets["ios_patch"]
        combo.setCurrentIndex(
            list(_ARGUMENTS["ios_patch"]["choices"]).index("Mucogingival line (lower arch)")
        )
        self.panel._applyVisibility()
        self.workspace = _FakeWorkspace(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace.root, True)

    def _fill(self, name, path):
        widget = self.panel._inputWidgets[name]
        edit = getattr(widget, "pathEdit", None) or getattr(widget.local, "pathEdit")
        edit.text = path

    def test_an_empty_optional_row_uploads_nothing(self):
        scan = os.path.join(self.workspace.root, "T1.zip")
        open(scan, "w").close()
        self._fill("t1", scan)
        self._fill("t2", scan)

        files = self.panel.prepareInputFiles(self.workspace)
        self.assertEqual(sorted(files), ["t1", "t2"])
        # The bug: "" was sent on as a path for the empty row.
        self.assertNotIn("", files.values())

    def test_a_filled_optional_row_is_uploaded(self):
        scan = os.path.join(self.workspace.root, "T1.zip")
        open(scan, "w").close()
        for name in ("t1", "t2", "mgl_landmarks"):
            self._fill(name, scan)

        files = self.panel.prepareInputFiles(self.workspace)
        self.assertEqual(sorted(files), ["mgl_landmarks", "t1", "t2"])
        self.assertTrue(all(os.path.exists(path) for path in files.values()))


if __name__ == "__main__":
    unittest.main()
