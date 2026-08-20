"""Unit tests for the AutoMatrix module's client behaviour - run outside Slicer,
with `qt`/`ctk`/`slicer` stubbed (ServerToolsCore/Testing/Python/qt_stubs.py).

What is tested here is what the generic ServerToolsCore tests cannot cover,
because it depends on AutoMatrix's own schema: that a scan folder and a matrix
folder are between them a complete request, that all three path arguments get a
picker taking a folder without this module declaring anything, and that a panel
with no mode really does show every field on every run.

That last one is the point of `TestNothingIsHidden`. AutoMatrix is the one
migrated tool whose schema carries no `visible_when` at all, and "no conditions"
is indistinguishable from "the conditions were lost" unless something asserts
it: every argument is read on every run, so a hidden field would be a parameter
silently left at its default.

`AutoMatrix.py` itself is deliberately NOT imported: it subclasses
ScriptedLoadableModule and ServerToolWidgetBase, which need a real Slicer.
Testing it would mean stubbing Slicer's module framework, at which point the
test would be measuring the stub. Its declarations are read out of the source
with `ast` instead - which is drift-proof in a way restating them here is not.

Usage:
    python3 -m unittest AutoMatrix/Testing/Python/test_automatrix_client.py
"""

import ast
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

# The server's GET /tools payload for AutoMatrix, verbatim: generated from the
# tool's own `scripts/describe.py` output and put through the same reduction
# `registry/schema_tool.py` applies (output_dir dropped, a Literal folded into
# `choices`, a path left without extensions). Kept here as a fixture so the
# panel can be tested without a running server; if the tool's signature
# changes, these tests are what notices.
AUTOMATRIX_SCHEMA = {
    "name": "AutoMatrix",
    "arguments": {
        "scans": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": True,
            "description": "The files to move -- a `.nii`, `.nii.gz` or `.nrrd` volume, a Slicer `.mrk.json` landmark file, or a folder of them for a batch. A folder is searched recursively and the tree is rebuilt under the output directory.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Scans or landmarks",
            "section": "Inputs",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "matrices": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": True,
            "description": "The transforms to apply: a `.tfm`, `.h5`, `.mat` or `.txt` ITK transform, or a folder of them. A folder is paired with the scans by patient key, so `P1_MAND_Or.tfm` is applied to `P1_T1_scan.nii.gz`; a single file is applied to every patient, which is how one mirroring matrix serves a whole cohort.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Matrices",
            "section": "Inputs",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "suffix": {
            "type": "str",
            "types": [
                "str"
            ],
            "required": False,
            "description": "Added to every output file name, before the extension, so a result never overwrites the scan it came from.",
            "server_selectable": None,
            "choices": None,
            "initial": "_apply",
            "extensions": None,
            "label": "Suffix",
            "section": "Output names",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "reference": {
            "type": "path",
            "types": [
                "path"
            ],
            "required": False,
            "description": "Optional volume whose grid every resampled result lands on. A cohort sharing one reference comes out voxel-aligned and can be compared; left empty, each scan is resampled on its own grid. Ignored for landmarks, and by a mirroring matrix, which is applied in the scan's own space.",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Reference volume",
            "section": "Resampling",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "add_matrix_name": {
            "type": "bool",
            "types": [
                "bool"
            ],
            "required": False,
            "description": "Also put the matrix's file name in the output's, which is what tells two results apart when a scan is moved through several matrices in one run.",
            "server_selectable": None,
            "choices": None,
            "initial": False,
            "extensions": None,
            "label": "Add the matrix name",
            "section": "Output names",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "from_areg": {
            "type": "bool",
            "types": [
                "bool"
            ],
            "required": False,
            "description": "Read the matrix for each LANDMARK file out of an AREG output tree instead of pairing by name: the `_CB`, `_L` or `_U` marker in the file's name picks the region, and the matrix is taken from `<matrices>/<region>/<patient>_OutReg/`. Volumes are still paired by name.",
            "server_selectable": None,
            "choices": None,
            "initial": False,
            "extensions": None,
            "label": "Matrices come from AREG",
            "section": "Inputs",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        },
        "is_segmentation": {
            "type": "bool",
            "types": [
                "bool"
            ],
            "required": False,
            "description": "Resample with nearest-neighbour instead of linear interpolation. A segmentation interpolated linearly comes back with label values that were never in it.",
            "server_selectable": None,
            "choices": None,
            "initial": False,
            "extensions": None,
            "label": "Input is a segmentation",
            "section": "Resampling",
            "visible_when": None,
            "options_when": None,
            "hidden": False,
            "ui": None,
            "groups": None
        }
    },
    "output_kind": "files"
}


def _argument(name: str) -> dict:
    return AUTOMATRIX_SCHEMA["arguments"][name]


def _module_source() -> str:
    with open(os.path.join(_REPO_ROOT, "AutoMatrix", "AutoMatrix.py"), encoding="utf-8") as handle:
        return handle.read()


def _class_attribute(name: str):
    """A literal class attribute of AutoMatrixWidget, read without importing."""
    tree = ast.parse(_module_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AutoMatrixWidget":
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        if isinstance(target, ast.Name) and target.id == name:
                            return ast.literal_eval(statement.value)
    raise AssertionError(f"AutoMatrixWidget declares no {name}")


class TestToolName(unittest.TestCase):
    """The name the module calls, which no schema can check for it."""

    def test_it_names_the_tool_the_server_serves(self):
        self.assertEqual(_class_attribute("TOOL_NAME"), AUTOMATRIX_SCHEMA["name"])

    def test_the_lowercase_cli_spelling_would_still_be_found(self):
        """`_canonical_tool_name` matches on case and separators.

        Upstream spells the module `AutoMatrix` and its CLI folder
        `Automatrix_CLI`, with a lowercase m. A respelling like that is
        absorbed; a SPLIT would not be, and there is none here - AutoMatrix is
        one tool, not several.
        """
        canonical = client_module._canonical_tool_name
        self.assertEqual(canonical("AutoMatrix"), canonical("Automatrix"))
        self.assertEqual(canonical("AutoMatrix"), canonical("Auto_Matrix"))


class TestNothingIsHidden(unittest.TestCase):
    """AutoMatrix has no mode, so every argument shows on every run."""

    def test_no_argument_carries_a_condition(self):
        for name, spec in AUTOMATRIX_SCHEMA["arguments"].items():
            self.assertIsNone(spec.get("visible_when"), name)
            self.assertIsNone(spec.get("options_when"), name)

    def test_every_argument_is_visible(self):
        visible = {
            name for name, spec in AUTOMATRIX_SCHEMA["arguments"].items()
            if formgen.is_visible(spec, {})
        }
        self.assertEqual(visible, set(AUTOMATRIX_SCHEMA["arguments"]))

    def test_there_is_no_controlling_argument(self):
        self.assertEqual(
            formgen.controlling_arguments(AUTOMATRIX_SCHEMA["arguments"]), set())

    def test_nothing_is_marked_hidden(self):
        for name, spec in AUTOMATRIX_SCHEMA["arguments"].items():
            self.assertFalse(spec.get("hidden"), name)


class TestPanelLayout(unittest.TestCase):
    """The sections are what the old hand-written .ui used to arrange."""

    def test_every_argument_has_a_label_and_a_section(self):
        for name, spec in AUTOMATRIX_SCHEMA["arguments"].items():
            self.assertTrue(spec.get("label"), name)
            self.assertTrue(spec.get("section"), name)

    def test_the_two_paths_the_user_must_fill_in_sit_together(self):
        sections = {name: spec["section"]
                    for name, spec in AUTOMATRIX_SCHEMA["arguments"].items()}
        self.assertEqual(sections["scans"], sections["matrices"])
        # The AREG switch reinterprets the matrix folder, so it belongs beside
        # it rather than among the resampling options.
        self.assertEqual(sections["from_areg"], sections["matrices"])
        self.assertEqual(sections["reference"], sections["is_segmentation"])


class TestInputPickers(unittest.TestCase):
    """A packaged tool's `path` takes a folder, and the client knows it."""

    def test_a_path_argument_is_a_file_argument(self):
        self.assertTrue(client_module.is_file_type(_argument("scans")["type"]))

    def test_every_path_accepts_a_folder_without_any_override(self):
        # A cohort is a folder and one case is a file, for all three. No
        # FILE_INPUTS is declared in AutoMatrix.py and none is needed; this is
        # what would break if one were added that said otherwise.
        modes = formgen.file_input_modes(AUTOMATRIX_SCHEMA["arguments"])
        self.assertEqual(set(modes), {"scans", "matrices", "reference"})
        for name in modes:
            self.assertEqual(modes[name], "file_or_folder", name)

    def test_a_single_matrix_file_is_a_real_use_and_not_an_accident(self):
        """One named matrix is applied to every patient - the mirroring case.

        Which is why `matrices` must keep the file half of its picker: forcing
        a folder here would take away the only way to run a mirror.
        """
        self.assertTrue(client_module.accepts_folder(_argument("matrices")))
        self.assertTrue(client_module.is_file_type(_argument("matrices")["type"]))

    def test_the_module_declares_no_file_inputs(self):
        self.assertNotIn("FILE_INPUTS = ", _module_source())

    def test_a_path_picker_offers_every_extension(self):
        # A packaged tool's path publishes no extensions: the server falls back
        # to its own ALLOWED_EXTENSIONS, so the dialog must not filter. It
        # matters more here than elsewhere - the matrix picker has to show
        # .tfm, .h5, .mat and .txt, none of which is an image.
        self.assertEqual(formgen.file_extensions_for(_argument("matrices")), ())


class TestOneRequest(unittest.TestCase):
    """`scans` + `matrices` alone is a complete, valid request."""

    def test_two_folders_alone_satisfy_the_schema(self):
        ToolServerClient._validate_against_schema(
            AUTOMATRIX_SCHEMA, {}, {"scans": "/tmp/scans.zip", "matrices": "/tmp/mat.zip"})

    def test_a_missing_matrix_folder_is_caught_before_the_round_trip(self):
        from ServerToolsCoreLib.errors import ServerToolError

        with self.assertRaises(ServerToolError):
            ToolServerClient._validate_against_schema(
                AUTOMATRIX_SCHEMA, {}, {"scans": "/tmp/scans.zip"})

    def test_a_reference_volume_is_optional(self):
        ToolServerClient._validate_against_schema(
            AUTOMATRIX_SCHEMA,
            {"is_segmentation": True},
            {"scans": "/tmp/scans.zip", "matrices": "/tmp/mat.zip",
             "reference": "/tmp/ref.nii.gz"},
        )

    def test_the_defaults_are_the_ones_the_tool_starts_from(self):
        # A suffix is what stops a result overwriting the scan it came from, so
        # an empty one arriving from a stale client would be destructive. The
        # tool's own default is what the panel must pre-fill.
        self.assertEqual(_argument("suffix")["initial"], "_apply")
        for name in ("add_matrix_name", "from_areg", "is_segmentation"):
            self.assertIs(_argument(name)["initial"], False)
        self.assertIsNone(_argument("reference")["initial"])

    def test_the_result_is_an_archive_to_unpack(self):
        # output_kind "files": the input tree rebuilt, one moved file per scan
        # and matrix, plus the report. Only "save_as" can mean anything for it.
        self.assertEqual(
            formgen.result_kind_for(AUTOMATRIX_SCHEMA["output_kind"]), "save_as")

    def test_the_module_lets_the_schema_decide_the_result_kind(self):
        self.assertNotIn("RESULT_KIND = ", _module_source())

    def test_the_module_declares_no_test_data(self):
        # The old panel's only download was the Mirror matrix, which is a
        # matrix and not test data; it is named like any other file now.
        self.assertNotIn("TEST_DATA = ", _module_source())


class TestResultLoading(unittest.TestCase):
    """What the module offers to load, and what it deliberately does not."""

    def test_a_moved_scan_loads_as_a_volume_not_a_segmentation(self):
        loadable = dict(_class_attribute("_LOADABLE"))
        self.assertEqual(loadable["*.nii.gz"], "volume")
        self.assertEqual(loadable["*.nrrd"], "volume")
        # AutoMatrix resamples a label map, it does not create one: loading a
        # mask as a segmentation node would relabel a file already labelled.
        self.assertNotIn("segmentation", set(loadable.values()))

    def test_moved_landmarks_are_offered_too(self):
        loadable = dict(_class_attribute("_LOADABLE"))
        self.assertEqual(loadable["*.mrk.json"], "markups")

    def test_the_report_is_not_loaded_as_markups(self):
        """`AutoMatrix_report.json` is a .json, not a .mrk.json."""
        patterns = [pattern for pattern, _kind in _class_attribute("_LOADABLE")]
        self.assertNotIn("*.json", patterns)

    def test_a_cohort_is_not_loaded_wholesale(self):
        self.assertIsInstance(_class_attribute("MAX_RESULTS_TO_LOAD"), int)
        self.assertGreater(_class_attribute("MAX_RESULTS_TO_LOAD"), 0)


class TestNoSupervisedCall(unittest.TestCase):
    """AutoMatrix drives no other tool, and the schema says so by omission."""

    def test_the_schema_publishes_no_calls(self):
        self.assertNotIn("calls", AUTOMATRIX_SCHEMA)


if __name__ == "__main__":
    unittest.main()
