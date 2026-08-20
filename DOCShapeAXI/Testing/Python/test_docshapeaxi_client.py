"""Unit tests for the DOCShapeAXI module's client behaviour - run outside
Slicer, with `qt`/`ctk`/`slicer` stubbed
(ServerToolsCore/Testing/Python/qt_stubs.py).

What is tested here is what the generic ServerToolsCore tests cannot cover,
because it depends on DOCShapeAXI's own schema: that the grade combo box
narrows itself to the anatomy, that a surface folder and a checkpoint bundle
alone are a complete request, and that what the module offers to load is the
explainability surfaces rather than the prediction table.

`DOCShapeAXI.py` itself is deliberately NOT imported: it subclasses
ScriptedLoadableModule and ServerToolWidgetBase, which need a real Slicer. Its
declarations are read out of the source with `ast` instead.

Usage:
    python3 -m unittest DOCShapeAXI/Testing/Python/test_docshapeaxi_client.py
"""

import ast
import os
import sys
import unittest

_HERE = os.path.abspath(os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_CORE = os.path.join(_REPO_ROOT, "ServerToolsCore")
sys.path.insert(0, os.path.join(_CORE, "Testing", "Python"))
sys.path.insert(0, _CORE)

import qt_stubs

qt, ctk = qt_stubs.install()

from ServerToolsCoreLib import client as client_module
from ServerToolsCoreLib import formgen
from ServerToolsCoreLib.client import ToolServerClient

# The server's GET /tools payload for DOCShapeAXI, generated from the tool's
# own `scripts/describe.py` output and put through the same reduction
# `registry/schema_tool.py` applies.
DOCSHAPEAXI_SCHEMA = {
    "name": "DOCShapeAXI",
    "arguments": {
        "meshes": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": True,
            "description": "One surface (.vtk/.vtp/.stl/.obj), or a folder of them for a batch. Folders are searched recursively.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Surfaces",
            "section": "Inputs",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "model": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": True,
            "description": "The checkpoint bundle hosted by the server, holding one `.ckpt` per anatomy and grade. Which one is used follows from `data_type` and `task`, so the bundle is named rather than the file.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Checkpoint bundle",
            "section": "Inputs",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "data_type": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": False,
            "description": "The anatomy being graded. It decides which model reads the surface, and with `task` which grades that model can give.",
            "server_selectable": None,
            "choices": {
                "Mandibular Condyle": True,
                "Nasopharynx Airway Obstruction": False,
                "Alveolar Bone Defect in Cleft": False
            },
            "initial": None,
            "extensions": None,
            "label": "Anatomy",
            "section": "Grading",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": "inline",
            "groups": None
        },
        "task": {
            "type": "choice",
            "types": [
                "choice"
            ],
            "required": False,
            "description": "What to grade. The airway has a model for each: binary (obstructed or not), severity (four grades) and regression (a continuous score). The mandibular condyle and the alveolar cleft have one four-grade model each, so `severity` is their only task -- asking for another is refused rather than quietly answered with the four-grade model.",
            "server_selectable": None,
            "choices": {
                "binary": False,
                "severity": True,
                "regression": False
            },
            "initial": None,
            "extensions": None,
            "label": "Grade",
            "section": "Grading",
            "visible_when": None,
            "options_when": {
                "data_type": {
                    "Mandibular Condyle": [
                        "severity"
                    ],
                    "Nasopharynx Airway Obstruction": [
                        "binary",
                        "severity",
                        "regression"
                    ],
                    "Alveolar Bone Defect in Cleft": [
                        "severity"
                    ]
                }
            },
            "hidden": False,
            "ui": "inline",
            "groups": None
        },
        "explainability": {
            "type": "bool",
            "types": [
                "bool"
            ],
            "required": False,
            "description": "Also write each surface again with a GradCAM array per grade on it, saying which part of the surface moved the model. This roughly doubles the run; leaving it off gives the grades alone.",
            "server_selectable": None,
            "choices": None,
            "initial": True,
            "extensions": None,
            "label": "Also write GradCAM surfaces",
            "section": "Grading",
            "visible_when": None,
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
            "description": "\"cuda\" or \"cpu\". CUDA falls back to CPU when no card is visible, with a warning.",
            "server_selectable": None,
            "choices": {
                "cuda": True,
                "cpu": False
            },
            "initial": None,
            "extensions": None,
            "label": "Device",
            "section": "Compute",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "num_workers": {
            "type": "int",
            "types": [
                "int"
            ],
            "required": False,
            "description": "Loader processes used to read and transform surfaces. 0 reads them in the main process, which is what to use when a machine is short of shared memory.",
            "server_selectable": None,
            "choices": None,
            "initial": 4,
            "extensions": None,
            "label": "Loader workers",
            "section": "Compute",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        }
    },
    "output_kind": "files",
    "calls": []
}


CONDYLE = "Mandibular Condyle"
AIRWAY = "Nasopharynx Airway Obstruction"
CLEFT = "Alveolar Bone Defect in Cleft"


def _argument(name: str) -> dict:
    return DOCSHAPEAXI_SCHEMA["arguments"][name]


def _module_source() -> str:
    with open(os.path.join(_REPO_ROOT, "DOCShapeAXI", "DOCShapeAXI.py"), encoding="utf-8") as handle:
        return handle.read()


def _class_attribute(name: str):
    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DOCShapeAXIWidget":
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        if isinstance(target, ast.Name) and target.id == name:
                            return ast.literal_eval(statement.value)
    raise AssertionError(f"DOCShapeAXIWidget declares no {name}")


class TestToolName(unittest.TestCase):
    def test_it_names_the_tool_the_server_serves(self):
        self.assertEqual(_class_attribute("TOOL_NAME"), DOCSHAPEAXI_SCHEMA["name"])


class TestGradeNarrowing(unittest.TestCase):
    """`options_when` on `task`: the grades an anatomy actually has a model for.

    The old panel offered all three for every anatomy and reached the
    four-class model regardless, so a "binary" grade on a condyle was a
    four-grade number wearing the wrong label. The tool refuses that pair now,
    and this is what keeps a client from ever asking for it.
    """

    def test_the_airway_offers_all_three_grades(self):
        self.assertEqual(
            formgen.allowed_options(_argument("task"), {"data_type": AIRWAY}),
            ["binary", "severity", "regression"],
        )

    def test_the_condyle_and_the_cleft_offer_severity_alone(self):
        for anatomy in (CONDYLE, CLEFT):
            self.assertEqual(
                formgen.allowed_options(_argument("task"), {"data_type": anatomy}),
                ["severity"],
                anatomy,
            )

    def test_an_unanswered_anatomy_does_not_empty_the_box(self):
        """A rule that cannot be evaluated must not leave nothing to pick."""
        self.assertIsNone(formgen.allowed_options(_argument("task"), {}))

    def test_every_narrowed_option_is_one_the_argument_publishes(self):
        published = set(_argument("task")["choices"])
        for anatomy, allowed in _argument("task")["options_when"]["data_type"].items():
            self.assertLessEqual(set(allowed), published, anatomy)

    def test_the_anatomy_is_what_the_panel_re_evaluates_on(self):
        self.assertEqual(
            formgen.controlling_arguments(DOCSHAPEAXI_SCHEMA["arguments"]),
            {"data_type"},
        )

    def test_the_three_anatomies_are_the_ones_upstream_offered(self):
        self.assertEqual(list(_argument("data_type")["choices"]), [CONDYLE, AIRWAY, CLEFT])


class TestInputPickers(unittest.TestCase):
    def test_surfaces_and_the_bundle_both_take_a_folder(self):
        modes = formgen.file_input_modes(DOCSHAPEAXI_SCHEMA["arguments"])
        self.assertEqual(modes["meshes"], "file_or_folder")
        self.assertEqual(modes["model"], "file_or_folder")

    def test_the_module_declares_no_file_inputs(self):
        self.assertNotIn("FILE_INPUTS = ", _module_source())

    def test_a_path_picker_does_not_filter_by_extension(self):
        # A packaged tool's path publishes no extensions; the server falls back
        # to its own ALLOWED_EXTENSIONS.
        self.assertEqual(formgen.file_extensions_for(_argument("meshes")), ())


class TestOneRequest(unittest.TestCase):
    def test_surfaces_and_a_bundle_alone_satisfy_the_schema(self):
        ToolServerClient._validate_against_schema(
            DOCSHAPEAXI_SCHEMA, {}, {"meshes": "/tmp/cohort.zip", "model": "/tmp/bundle.zip"})

    def test_a_missing_bundle_is_caught_before_the_round_trip(self):
        from ServerToolsCoreLib.errors import ServerToolError

        with self.assertRaises(ServerToolError):
            ToolServerClient._validate_against_schema(
                DOCSHAPEAXI_SCHEMA, {}, {"meshes": "/tmp/cohort.zip"})

    def test_explainability_is_on_by_default(self):
        """A grade nobody can check is a grade nobody should act on."""
        self.assertIs(_argument("explainability")["initial"], True)

    def test_the_result_is_an_archive_to_unpack(self):
        self.assertEqual(formgen.result_kind_for(DOCSHAPEAXI_SCHEMA["output_kind"]), "save_as")

    def test_the_module_lets_the_schema_decide_the_result_kind(self):
        self.assertNotIn("RESULT_KIND = ", _module_source())


class TestResultLoading(unittest.TestCase):
    def test_the_explainability_surfaces_load_as_models(self):
        loadable = dict(_class_attribute("_LOADABLE"))
        self.assertEqual(set(loadable.values()), {"model"})
        self.assertIn("*.vtk", loadable)

    def test_the_prediction_table_is_not_loaded_as_a_node(self):
        patterns = [pattern for pattern, _kind in _class_attribute("_LOADABLE")]
        self.assertNotIn("*.csv", patterns)


class TestNoSupervisedCall(unittest.TestCase):
    def test_this_tool_asks_for_no_other(self):
        self.assertEqual(DOCSHAPEAXI_SCHEMA["calls"], [])


if __name__ == "__main__":
    unittest.main()
