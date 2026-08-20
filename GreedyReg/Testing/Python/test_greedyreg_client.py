"""Unit tests for the GreedyReg module's client behaviour - run outside Slicer,
with `qt`/`ctk`/`slicer` stubbed (ServerToolsCore/Testing/Python/qt_stubs.py).

What is tested here is what the generic ServerToolsCore tests cannot cover,
because it depends on GreedyReg's own schema: that the mode conditions really
hide the arguments the chosen mode never reads, that two folders and nothing
else is a complete request, and that a packaged tool's `path` arguments get a
picker taking a folder without this module declaring anything.

`GreedyReg.py` itself is deliberately NOT imported: it subclasses
ScriptedLoadableModule and ServerToolWidgetBase, which need a real Slicer.
Testing it would mean stubbing Slicer's module framework, at which point the
test would be measuring the stub. Its declarations are read out of the source
with `ast` instead - which is drift-proof in a way restating them here is not.

Usage:
    python3 -m unittest GreedyReg/Testing/Python/test_greedyreg_client.py
"""

import ast
import json
import os
import sys
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

from ServerToolsCoreLib import client as client_module
from ServerToolsCoreLib import formgen
from ServerToolsCoreLib.client import ToolServerClient

# The server's GET /tools payload for GreedyReg, verbatim: generated from the
# tool's own `scripts/describe.py` output and put through the same reduction
# `registry/schema_tool.py` applies (output_dir dropped, a Literal folded into
# `choices`, a path left without extensions). Kept here as a fixture so the
# panel can be tested without a running server; if the tool's signature
# changes, these tests are what notices.
GREEDYREG_SCHEMA = {
    "name": "GreedyReg",
    "arguments": {
        "t1": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": True,
            "description": "The baseline (fixed) scans -- one `.nii`/`.nii.gz` volume, or a folder of them for a batch. A folder is paired with T2 on the leading letters-and-digits of each file name, so `MG01_T1.nii.gz` matches `MG01_T2.nii.gz`. Not searched recursively: a previous run's output must not come back in as an input.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "T1 (baseline)",
            "section": "Inputs",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "t2": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": True,
            "description": "The follow-up (moving) scans, paired to T1 by that same key. Give two folders for a batch, or two files for a single pair.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "T2 (follow-up)",
            "section": "Inputs",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "mode": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": False,
            "description": "Greedy registers directly, which needs the two timepoints already roughly aligned. Landmark only aligns T2 onto T1 from anatomical landmarks, writing a repositioned volume without touching a voxel. Landmark + Greedy does the second and hands its transform to the first, which is the sequence the module instructs you to run by hand when the timepoints are far apart.",
            "server_selectable": None,
            "choices": {
                "Greedy": True,
                "Landmark": False,
                "Landmark + Greedy": False
            },
            "initial": None,
            "extensions": None,
            "label": "Mode",
            "section": "Inputs",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "metric": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": False,
            "description": "The similarity Greedy optimises. NMI tolerates a difference in intensity scaling between the timepoints, NCC assumes a linear relation, SSD assumes an identical one.",
            "server_selectable": None,
            "choices": {
                "NMI": True,
                "NCC": False,
                "SSD": False
            },
            "initial": None,
            "extensions": None,
            "label": "Similarity metric",
            "section": "Registration",
            "visible_when": {
                "mode": [
                    "Greedy",
                    "Landmark + Greedy"
                ]
            },
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "transform_type": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": False,
            "description": "Rigid keeps the follow-up's shape and size (6 degrees of freedom); Affine also allows scaling and shear (12).",
            "server_selectable": None,
            "choices": {
                "Rigid": True,
                "Affine": False
            },
            "initial": None,
            "extensions": None,
            "label": "Degrees of freedom",
            "section": "Registration",
            "visible_when": {
                "mode": [
                    "Greedy",
                    "Landmark + Greedy"
                ]
            },
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "masks": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": False,
            "description": "Optional folder of T1-space masks, one per patient key, telling Greedy which voxels to score. Registering on what has NOT changed between the timepoints is what makes the result mean anything. Binarised before use, so a multi-label segmentation is accepted.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "T1 masks",
            "section": "Registration",
            "visible_when": {
                "mode": [
                    "Greedy",
                    "Landmark + Greedy"
                ]
            },
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "init": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": False,
            "description": "Optional folder of per-patient `{key}*.mat` starting transforms. A case with no matching file starts from identity. Ignored in the landmark modes, which compute their own.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Initial transforms",
            "section": "Registration",
            "visible_when": {
                "mode": "Greedy"
            },
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "region": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": False,
            "description": "Which anatomy the landmark modes align on -- the mandible (MANDMASK), the maxilla (MAXMASK) or the cranial base (CBMASK). Each names its own landmark set, and at least three of them must be found on both scans.",
            "server_selectable": None,
            "choices": {
                "MANDMASK": True,
                "MAXMASK": False,
                "CBMASK": False
            },
            "initial": None,
            "extensions": None,
            "label": "Align on",
            "section": "Landmark alignment",
            "visible_when": {
                "mode": [
                    "Landmark",
                    "Landmark + Greedy"
                ]
            },
            "options_when": None,
            "hidden": False,
            "ui": "inline",
            "groups": None
        },
        "landmark_model": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": False,
            "description": "The ALI landmark model bundle. Required by the landmark modes, unused by Greedy mode. Named rather than resolved here: a tool does not go looking for weights on the server's disk.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "ALI model bundle",
            "section": "Landmark alignment",
            "visible_when": {
                "mode": [
                    "Landmark",
                    "Landmark + Greedy"
                ]
            },
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "device": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": False,
            "description": "\"cuda\" or \"cpu\", passed to the landmark tool. Greedy itself is CPU-only and ignores this.",
            "server_selectable": None,
            "choices": {
                "cuda": True,
                "cpu": False
            },
            "initial": None,
            "extensions": None,
            "label": "Device",
            "section": "Landmark alignment",
            "visible_when": {
                "mode": [
                    "Landmark",
                    "Landmark + Greedy"
                ]
            },
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        }
    },
    "output_kind": "files",
    "calls": [
        "ALI_CBCT"
    ]
}


def _argument(name: str) -> dict:
    return GREEDYREG_SCHEMA["arguments"][name]


def _module_source() -> str:
    with open(os.path.join(_REPO_ROOT, "GreedyReg", "GreedyReg.py"), encoding="utf-8") as handle:
        return handle.read()


def _class_attribute(name: str):
    """A literal class attribute of GreedyRegWidget, read without importing."""
    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "GreedyRegWidget":
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        if isinstance(target, ast.Name) and target.id == name:
                            return ast.literal_eval(statement.value)
    raise AssertionError(f"GreedyRegWidget declares no {name}")


class TestToolName(unittest.TestCase):
    """The name the module calls, which no schema can check for it."""

    def test_it_names_the_tool_the_server_serves(self):
        self.assertEqual(_class_attribute("TOOL_NAME"), GREEDYREG_SCHEMA["name"])

    def test_a_respelling_would_still_be_found(self):
        """`_canonical_tool_name` matches on case and separators.

        Which is why "GreedyReg" finds a tool named "Greedy_Reg" and vice versa.
        A SPLIT would not be absorbed, and there is none here - GreedyReg is one
        tool with three modes, not three tools.
        """
        canonical = client_module._canonical_tool_name
        self.assertEqual(canonical("GreedyReg"), canonical("Greedy_Reg"))


class TestModeScoping(unittest.TestCase):
    """Every argument that belongs to one mode is hidden in the others.

    This is the whole reason the upstream module had two tabs, and it is now
    the schema's `visible_when`. If a condition ever names a mode that is not
    an option, the panel silently shows the field always - so the options are
    checked against the ones `mode` publishes.
    """

    def _visible(self, mode: str) -> set:
        values = {"mode": mode}
        return {
            name for name, spec in GREEDYREG_SCHEMA["arguments"].items()
            if formgen.is_visible(spec, values)
        }

    def test_greedy_mode_hides_the_landmark_arguments(self):
        visible = self._visible("Greedy")
        self.assertIn("metric", visible)
        self.assertIn("init", visible)
        for hidden in ("region", "landmark_model", "device"):
            self.assertNotIn(hidden, visible)

    def test_landmark_mode_hides_the_greedy_arguments(self):
        visible = self._visible("Landmark")
        self.assertIn("region", visible)
        self.assertIn("landmark_model", visible)
        for hidden in ("metric", "transform_type", "masks", "init"):
            self.assertNotIn(hidden, visible)

    def test_the_combined_mode_shows_both_but_not_the_supplied_init(self):
        visible = self._visible("Landmark + Greedy")
        self.assertIn("metric", visible)
        self.assertIn("landmark_model", visible)
        # It computes its own initialisation, and would ignore a supplied one.
        self.assertNotIn("init", visible)

    def test_the_two_inputs_are_always_shown(self):
        for mode in GREEDYREG_SCHEMA["arguments"]["mode"]["choices"]:
            self.assertLessEqual({"t1", "t2", "mode"}, self._visible(mode))

    def test_every_condition_names_a_real_argument_and_real_options(self):
        options = set(GREEDYREG_SCHEMA["arguments"]["mode"]["choices"])
        for name, spec in GREEDYREG_SCHEMA["arguments"].items():
            condition = spec.get("visible_when")
            if not condition:
                continue
            for controlling, expected in condition.items():
                self.assertIn(controlling, GREEDYREG_SCHEMA["arguments"], name)
                expected = expected if isinstance(expected, list) else [expected]
                self.assertLessEqual(set(expected), options, name)

    def test_mode_is_recognised_as_the_controlling_argument(self):
        self.assertEqual(
            formgen.controlling_arguments(GREEDYREG_SCHEMA["arguments"]), {"mode"})


class TestInputPickers(unittest.TestCase):
    """A packaged tool's `path` takes a folder, and the client knows it."""

    def test_a_path_argument_is_a_file_argument(self):
        self.assertTrue(client_module.is_file_type(_argument("t1")["type"]))

    def test_both_timepoints_accept_a_folder_without_any_override(self):
        # Two folders is a batch, two files is one pair. No FILE_INPUTS is
        # declared in GreedyReg.py and none is needed; this is what would break
        # if one were added that said otherwise.
        modes = formgen.file_input_modes(GREEDYREG_SCHEMA["arguments"])
        self.assertEqual(modes["t1"], "file_or_folder")
        self.assertEqual(modes["t2"], "file_or_folder")

    def test_the_optional_paths_get_a_picker_too(self):
        modes = formgen.file_input_modes(GREEDYREG_SCHEMA["arguments"])
        for name in ("masks", "init", "landmark_model"):
            self.assertEqual(modes[name], "file_or_folder", name)

    def test_the_module_declares_no_file_inputs(self):
        source = _module_source()
        self.assertNotIn("FILE_INPUTS = ", source)

    def test_a_path_picker_offers_every_extension(self):
        # A packaged tool's path publishes no extensions: the server falls back
        # to its own ALLOWED_EXTENSIONS, so the dialog must not filter.
        self.assertEqual(formgen.file_extensions_for(_argument("t1")), ())


class TestOneRequest(unittest.TestCase):
    """`t1` + `t2` alone is a complete, valid request."""

    def test_two_folders_alone_satisfy_the_schema(self):
        ToolServerClient._validate_against_schema(
            GREEDYREG_SCHEMA, {}, {"t1": "/tmp/T1.zip", "t2": "/tmp/T2.zip"})

    def test_a_missing_timepoint_is_caught_before_the_round_trip(self):
        from ServerToolsCoreLib.errors import ServerToolError

        with self.assertRaises(ServerToolError):
            ToolServerClient._validate_against_schema(
                GREEDYREG_SCHEMA, {}, {"t1": "/tmp/T1.zip"})

    def test_a_landmark_run_names_its_bundle(self):
        ToolServerClient._validate_against_schema(
            GREEDYREG_SCHEMA,
            {"mode": "Landmark"},
            {"t1": "/tmp/T1.zip", "t2": "/tmp/T2.zip", "landmark_model": "/tmp/ALI_CBCT_v2.zip"},
        )

    def test_the_result_is_an_archive_to_unpack(self):
        # output_kind "files": a registered volume and a transform per pair,
        # plus the report. Only "save_as" can mean anything for that.
        self.assertEqual(formgen.result_kind_for(GREEDYREG_SCHEMA["output_kind"]), "save_as")

    def test_the_module_lets_the_schema_decide_the_result_kind(self):
        self.assertNotIn("RESULT_KIND = ", _module_source())


class TestResultLoading(unittest.TestCase):
    """What the module offers to load, and what it deliberately does not."""

    def test_a_registered_scan_loads_as_a_volume_not_a_segmentation(self):
        loadable = dict(_class_attribute("_LOADABLE"))
        self.assertEqual(set(loadable.values()), {"volume"})
        self.assertIn("*.nii.gz", loadable)

    def test_the_transform_is_not_loaded(self):
        patterns = [pattern for pattern, _kind in _class_attribute("_LOADABLE")]
        self.assertNotIn("*.mat", patterns)
        self.assertNotIn("*_warp.mat", patterns)

    def test_a_cohort_is_not_loaded_wholesale(self):
        self.assertIsInstance(_class_attribute("MAX_RESULTS_TO_LOAD"), int)
        self.assertGreater(_class_attribute("MAX_RESULTS_TO_LOAD"), 0)


class TestSupervisedCall(unittest.TestCase):
    """The landmark modes cost a second tool, and the schema says so."""

    def test_the_schema_publishes_the_tool_it_calls(self):
        self.assertEqual(GREEDYREG_SCHEMA["calls"], ["ALI_CBCT"])


if __name__ == "__main__":
    unittest.main()
