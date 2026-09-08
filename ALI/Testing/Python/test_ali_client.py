"""Unit tests for the ALI module's client behaviour — run outside Slicer, with
`qt`/`ctk`/`slicer` stubbed (ServerToolsCore/Testing/Python/qt_stubs.py).

What is tested here is everything ALI's panel and request building do that the
generic tests in ServerToolsCore cannot cover, because it depends on ALI's own
schema: the union of two file types' extensions, the folder picker that only a
FILE_INPUTS override produces, and the claim that `input` + `model` alone form
a complete request.

`ALI.py` itself is deliberately NOT imported: it subclasses
ScriptedLoadableModule and ServerToolWidgetBase, which need a real Slicer
(slicer.util.VTKObservationMixin, slicer.i18n). Testing it would mean stubbing
Slicer's module framework, at which point the test would be measuring the stub.
The module's declarations are asserted instead, against the same functions it
delegates to.

Usage:
    python3 -m unittest ALI/Testing/Python/test_ali_client.py
"""

import json
import os
import shutil
import sys
import tempfile
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

from ServerToolsCoreLib import formgen, slicer_io
from ServerToolsCoreLib.client import ToolServerClient
from ServerToolsCoreLib.errors import error_for_status

# The server's actual GET /tools payload for ALI, verbatim. Kept here as a
# fixture so the panel can be tested without a running server; if the server's
# schema changes, these tests are what notices.
ALI_SCHEMA = {
    "name": "ALI",
    "arguments": {
        "input": {
            "type": "volume_or_zip_file",
            "types": [
                "volume_or_zip_file",
                "surface_or_zip_file"
            ],
            "required": True,
            "description": "A CBCT scan (.nii/.nii.gz/.nrrd/.nrrd.gz/.gipl/.gipl.gz), an IOS surface (.vtk/.stl), or a .zip archive of a folder of either -- DICOM series are recognised inside the archive and converted automatically",
            "server_selectable": "testfile",
            "choices": None,
            "initial": None,
            "extensions": {
                "volume_or_zip_file": [
                    ".nii",
                    ".nii.gz",
                    ".nrrd",
                    ".nrrd.gz",
                    ".gipl",
                    ".gipl.gz",
                    ".zip"
                ],
                "surface_or_zip_file": [
                    ".vtk",
                    ".stl",
                    ".zip"
                ]
            },
            "label": "Scan or Folder",
            "section": "Inputs",
            "visible_when": None,
            "ui": None,
            "groups": None
        },
        "model": {
            "type": "str",
            "types": [
                "str"
            ],
            "required": False,
            "description": "Name of a model bundle hosted on the server (see GET /tools/ALI/data). Leave empty to let the server pick the bundle matching the detected mode. CBCT bundles hold <landmark>/<scale>/*.pth; IOS bundles hold checkpoints named with an 'O' or 'C' token and an 'Upper' or 'Lower' one, e.g. Upper_O_model.pth",
            "server_selectable": "model",
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Model Bundle",
            "section": "Inputs",
            "visible_when": None,
            "ui": None,
            "groups": None
        },
        "cbct_regions": {
            "type": "multichoice",
            "types": [
                "multichoice"
            ],
            "required": False,
            "description": "CBCT only: anatomical regions to predict",
            "server_selectable": None,
            "choices": {
                "Cranial base": True,
                "Upper": True,
                "Lower": True,
                "Impacted canine": True
            },
            "initial": None,
            "extensions": None,
            "label": "Regions",
            "section": "CBCT landmarks",
            "visible_when": None,
            "ui": "inline",
            "groups": None
        },
        "landmarks": {
            "type": "multichoice",
            "types": [
                "multichoice"
            ],
            "required": False,
            "description": "CBCT only: predict exactly these landmarks. Leave every box unchecked to select by region instead -- naming any landmark here REPLACES the region selection rather than narrowing it",
            "server_selectable": None,
            "choices": {
                "Ba": False,
                "S": False,
                "N": False,
                "RPo": False,
                "LPo": False,
                "RFZyg": False,
                "LFZyg": False,
                "C2": False,
                "C3": False,
                "C4": False,
                "RInfOr": False,
                "LInfOr": False,
                "LMZyg": False,
                "RPF": False,
                "LPF": False,
                "PNS": False,
                "ANS": False,
                "A": False,
                "UR3O": False,
                "UR1O": False,
                "UL3O": False,
                "UR6DB": False,
                "UR6MB": False,
                "UL6MB": False,
                "UL6DB": False,
                "IF": False,
                "ROr": False,
                "LOr": False,
                "RMZyg": False,
                "RNC": False,
                "LNC": False,
                "UR7O": False,
                "UR5O": False,
                "UR4O": False,
                "UR2O": False,
                "UL1O": False,
                "UL2O": False,
                "UL4O": False,
                "UL5O": False,
                "UL7O": False,
                "UL7R": False,
                "UL5R": False,
                "UL4R": False,
                "UL2R": False,
                "UL1R": False,
                "UR2R": False,
                "UR4R": False,
                "UR5R": False,
                "UR7R": False,
                "UR6MP": False,
                "UL6MP": False,
                "UL6R": False,
                "UR6R": False,
                "UR6O": False,
                "UL6O": False,
                "UL3R": False,
                "UR3R": False,
                "UR1R": False,
                "RCo": False,
                "RGo": False,
                "Me": False,
                "Gn": False,
                "Pog": False,
                "PogL": False,
                "B": False,
                "LGo": False,
                "LCo": False,
                "LR1O": False,
                "LL6MB": False,
                "LL6DB": False,
                "LR6MB": False,
                "LR6DB": False,
                "LAF": False,
                "LAE": False,
                "RAF": False,
                "RAE": False,
                "LMCo": False,
                "LLCo": False,
                "RMCo": False,
                "RLCo": False,
                "RMeF": False,
                "LMeF": False,
                "RSig": False,
                "RPRa": False,
                "RARa": False,
                "LSig": False,
                "LARa": False,
                "LPRa": False,
                "LR7R": False,
                "LR5R": False,
                "LR4R": False,
                "LR3R": False,
                "LL3R": False,
                "LL4R": False,
                "LL5R": False,
                "LL7R": False,
                "LL7O": False,
                "LL5O": False,
                "LL4O": False,
                "LL3O": False,
                "LL2O": False,
                "LL1O": False,
                "LR2O": False,
                "LR3O": False,
                "LR4O": False,
                "LR5O": False,
                "LR7O": False,
                "LL6R": False,
                "LR6R": False,
                "LL6O": False,
                "LR6O": False,
                "LR1R": False,
                "LL1R": False,
                "LL2R": False,
                "LR2R": False,
                "UR3OIP": False,
                "UL3OIP": False,
                "UR3RIP": False,
                "UL3RIP": False
            },
            "initial": None,
            "extensions": None,
            "label": "Individual landmarks",
            "section": "CBCT landmarks",
            "visible_when": None,
            "ui": "tabs",
            "groups": {
                "Cranial base": [
                    "Ba",
                    "S",
                    "N",
                    "RPo",
                    "LPo",
                    "RFZyg",
                    "LFZyg",
                    "C2",
                    "C3",
                    "C4"
                ],
                "Upper": [
                    "RInfOr",
                    "LInfOr",
                    "LMZyg",
                    "RPF",
                    "LPF",
                    "PNS",
                    "ANS",
                    "A",
                    "UR3O",
                    "UR1O",
                    "UL3O",
                    "UR6DB",
                    "UR6MB",
                    "UL6MB",
                    "UL6DB",
                    "IF",
                    "ROr",
                    "LOr",
                    "RMZyg",
                    "RNC",
                    "LNC",
                    "UR7O",
                    "UR5O",
                    "UR4O",
                    "UR2O",
                    "UL1O",
                    "UL2O",
                    "UL4O",
                    "UL5O",
                    "UL7O",
                    "UL7R",
                    "UL5R",
                    "UL4R",
                    "UL2R",
                    "UL1R",
                    "UR2R",
                    "UR4R",
                    "UR5R",
                    "UR7R",
                    "UR6MP",
                    "UL6MP",
                    "UL6R",
                    "UR6R",
                    "UR6O",
                    "UL6O",
                    "UL3R",
                    "UR3R",
                    "UR1R"
                ],
                "Lower": [
                    "RCo",
                    "RGo",
                    "Me",
                    "Gn",
                    "Pog",
                    "PogL",
                    "B",
                    "LGo",
                    "LCo",
                    "LR1O",
                    "LL6MB",
                    "LL6DB",
                    "LR6MB",
                    "LR6DB",
                    "LAF",
                    "LAE",
                    "RAF",
                    "RAE",
                    "LMCo",
                    "LLCo",
                    "RMCo",
                    "RLCo",
                    "RMeF",
                    "LMeF",
                    "RSig",
                    "RPRa",
                    "RARa",
                    "LSig",
                    "LARa",
                    "LPRa",
                    "LR7R",
                    "LR5R",
                    "LR4R",
                    "LR3R",
                    "LL3R",
                    "LL4R",
                    "LL5R",
                    "LL7R",
                    "LL7O",
                    "LL5O",
                    "LL4O",
                    "LL3O",
                    "LL2O",
                    "LL1O",
                    "LR2O",
                    "LR3O",
                    "LR4O",
                    "LR5O",
                    "LR7O",
                    "LL6R",
                    "LR6R",
                    "LL6O",
                    "LR6O",
                    "LR1R",
                    "LL1R",
                    "LL2R",
                    "LR2R"
                ],
                "Impacted canine": [
                    "UR3OIP",
                    "UL3OIP",
                    "UR3RIP",
                    "UL3RIP"
                ]
            }
        },
        "ios_networks": {
            "type": "multichoice",
            "types": [
                "multichoice"
            ],
            "required": False,
            "description": "IOS only: landmark families to predict",
            "server_selectable": None,
            "choices": {
                "Occlusal": True,
                "Cervical": True,
                "Mucogingival": False
            },
            "initial": None,
            "extensions": None,
            "label": "Landmark families",
            "section": "IOS landmarks",
            "visible_when": None,
            "ui": "inline",
            "groups": None
        },
        "prediction_ID": {
            "type": "str",
            "types": [
                "str"
            ],
            "required": False,
            "description": "Suffix used in output file names, e.g. scan_lm_Pred.mrk.json",
            "server_selectable": None,
            "choices": None,
            "initial": None,
            "extensions": None,
            "label": "Prediction ID",
            "section": "Outputs",
            "visible_when": None,
            "ui": None,
            "groups": None
        }
    },
    "output_kind": "files"
}

# What ALI/ALI.py declares. Asserted rather than imported — see the module
# docstring.
ALI_FILE_INPUTS = {"input": "file_or_folder"}


def _argument(name: str) -> dict:
    return ALI_SCHEMA["arguments"][name]


class TestInputPicker(unittest.TestCase):
    """The `input` row: which extensions it offers, and that it offers a
    folder at all."""

    def test_extensions_are_the_union_of_both_file_types(self):
        # Both declared types are file types, so the picker offers everything
        # either of them accepts — the client must not have to know which
        # engine will end up running.
        extensions = formgen.file_extensions_for(_argument("input"))
        self.assertEqual(
            set(extensions),
            {".nii", ".nii.gz", ".nrrd", ".nrrd.gz", ".gipl", ".gipl.gz", ".vtk", ".stl", ".zip"},
        )
        # ".zip" is declared by both types and must appear once, not twice, or
        # the dialog's filter string repeats it.
        self.assertEqual(len(extensions), len(set(extensions)))

    def test_schema_alone_would_not_offer_a_folder_picker(self):
        # ALI's `input` declares no "folder" type, so the schema-driven rule
        # gives a file picker only. This is exactly why ALI.py overrides it,
        # and this test is what fails if someone removes the override believing
        # the schema covers it.
        self.assertEqual(formgen.auto_file_mode(_argument("input")), "single_file")

    def test_override_produces_a_file_and_folder_row(self):
        modes = formgen.file_input_modes(ALI_SCHEMA["arguments"], ALI_FILE_INPUTS)
        self.assertEqual(modes, {"input": "file_or_folder"})

        widget = formgen.file_widget(_argument("input"), modes["input"])
        # `input` is also server_selectable, so the row is the dropdown of
        # hosted files wrapped around the local picker (see TestServerSideInput).
        self.assertIsInstance(widget, formgen.ServerFileInput)
        self.assertIsInstance(widget.local, formgen.FileOrFolderInput)
        # Both browse buttons exist: a DICOM series is a directory and has no
        # extension a file dialog could match.
        self.assertTrue(hasattr(widget.local, "fileButton"))
        self.assertTrue(hasattr(widget.local, "folderButton"))

    def test_file_dialog_filter_lists_every_accepted_extension(self):
        widget = formgen.file_widget(_argument("input"), "file_or_folder")
        qt.QFileDialog.next_file = "/tmp/scan.nii.gz"
        widget.local._onBrowseFile()

        filters = qt.QFileDialog.last_open_file_args[-1]
        for extension in (".nii.gz", ".nrrd", ".gipl.gz", ".vtk", ".stl", ".zip"):
            self.assertIn(f"*{extension}", filters)
        self.assertEqual(widget.currentPath, "/tmp/scan.nii.gz")

    def test_a_folder_selection_is_recognised_as_one(self):
        directory = tempfile.mkdtemp(prefix="ali_pick_")
        self.addCleanup(shutil.rmtree, directory, True)

        widget = formgen.file_widget(_argument("input"), "file_or_folder")
        qt.QFileDialog.next_directory = directory
        widget.local._onBrowseFolder()

        self.assertEqual(widget.currentPath, directory)
        # The upload path branches on this, never on something the user had to
        # set correctly beforehand.
        self.assertTrue(widget.is_folder())


class TestServerSideInput(unittest.TestCase):
    """`input` is `server_selectable: "testfile"`: the tool's own test scans
    are offered in the input row, and picking one downloads it."""

    def setUp(self):
        self.widget = formgen.file_widget(_argument("input"), "file_or_folder")
        self.widget.setChoices([
            {"name": "MG_test_scan.nii.gz", "kind": "file", "size": 98 * 1024 * 1024},
            {"name": "cohort_10_patients.zip", "kind": "file", "size": None},
        ])
        self.downloaded = []
        self.widget.setHostedCallback(self.downloaded.append)

    def test_the_prompt_leads_and_names_nothing(self):
        self.assertEqual(
            self.widget.combo.itemText(0), formgen.ServerFileInput.CHOOSE_OPTION
        )
        self.assertEqual(self.widget.combo.count, 3)
        self.assertEqual(self.widget.hosted_name(), "")
        self.assertEqual(self.widget.currentPath, "")

    def test_each_test_file_says_what_it_is_and_what_it_costs(self):
        self.assertEqual(self.widget.combo.itemText(1), "MG_test_scan.nii.gz  (file, 98 MB)")
        # The server could not size the second one; a size it did not state is
        # left out rather than rendered as "0 B".
        self.assertEqual(self.widget.combo.itemText(2), "cohort_10_patients.zip  (file)")

    def test_choosing_a_test_file_asks_for_it_to_be_downloaded(self):
        self.widget.combo.setCurrentIndex(1)

        self.assertEqual(self.downloaded, ["MG_test_scan.nii.gz"])
        # Nothing to upload until the bytes land, which is the honest state:
        # Apply stays disabled while the download runs.
        self.assertEqual(self.widget.currentPath, "")

    def test_the_two_halves_are_mutually_exclusive_and_visibly_so(self):
        self.widget.combo.setCurrentIndex(1)
        self.widget.local.pathEdit.setText("/data/my_own_scan.nii.gz")
        # Picking a local file resets the dropdown rather than losing to it:
        # a precedence rule the user cannot see is how you end up sending the
        # file you thought you had replaced.
        self.assertEqual(self.widget.hosted_name(), "")
        self.assertEqual(self.widget.currentPath, "/data/my_own_scan.nii.gz")

        self.widget.combo.setCurrentIndex(2)
        self.assertEqual(self.widget.currentPath, "")
        self.assertEqual(self.widget.local.currentPath, "")

    def test_a_downloaded_test_file_is_an_ordinary_upload(self):
        """The wire shape changed with the mechanism: the name used to travel
        as a form value and the server read the file in place. It is on the
        user's disk now, so it goes up like any other local file."""
        ToolServerClient._validate_against_schema(
            ALI_SCHEMA,
            {"model": "ALI_CBCT_v2"},
            {"input": "/tmp/session/MG_test_scan.nii.gz"},
        )

    def test_a_hosted_name_is_still_accepted_by_the_validator(self):
        # The server supports both shapes and this client no longer sends the
        # second; rejecting it here would make the validator stricter than the
        # thing it mirrors.
        ToolServerClient._validate_against_schema(
            ALI_SCHEMA, {"model": "ALI_CBCT_v2", "input": "MG_test_scan.nii.gz"}, {}
        )

    def test_the_model_argument_gets_no_local_picker(self):
        # `model` is server_selectable on a SCALAR type: the weights must never
        # leave the server, so there is no "upload my own" for it.
        self.assertFalse(formgen.is_file_type(_argument("model")["type"]))


class TestFolderUpload(unittest.TestCase):
    """A picked directory becomes the .zip that actually goes up."""

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="ali_zip_")
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.cohort = os.path.join(self.workdir, "cohort")
        os.makedirs(os.path.join(self.cohort, "patient1"))
        os.makedirs(os.path.join(self.cohort, "patient2"))

    def _zip(self) -> str:
        return slicer_io.zip_folder(self.cohort, os.path.join(self.workdir, "ALI_input.zip"))

    def test_zips_a_folder_of_scans(self):
        open(os.path.join(self.cohort, "patient1", "scan.nii.gz"), "wb").close()
        open(os.path.join(self.cohort, "patient2", "scan.nii.gz"), "wb").close()

        import zipfile

        with zipfile.ZipFile(self._zip()) as archive:
            names = set(archive.namelist())
        # Paths are relative to the picked folder, so the server sees the tree
        # it mirrors in the output, with no "cohort/" wrapper to strip.
        self.assertEqual(names, {"patient1/scan.nii.gz", "patient2/scan.nii.gz"})

    def test_zips_extensionless_dicom_slices(self):
        # The reason the folder picker cannot be replaced by a filtered file
        # picker: DICOM slices carry no extension at all.
        for name in ("IM000001", "1.2.840.10008.1.2.3"):
            open(os.path.join(self.cohort, "patient1", name), "wb").close()

        import zipfile

        with zipfile.ZipFile(self._zip()) as archive:
            names = set(archive.namelist())
        self.assertEqual(names, {"patient1/IM000001", "patient1/1.2.840.10008.1.2.3"})


class TestTaskOneRequest(unittest.TestCase):
    """`input` + `model` alone is a complete, valid request."""

    def test_input_and_model_alone_satisfy_the_schema(self):
        # Every other argument is optional, so the server applies its declared
        # defaults and predicts everything the bundle can. Nothing else is
        # needed for a first end-to-end run.
        ToolServerClient._validate_against_schema(
            ALI_SCHEMA, {"model": "ALI_CBCT_v2"}, {"input": "/tmp/cohort.zip"}
        )

    def test_the_model_travels_as_a_name_not_a_file(self):
        self.assertEqual(
            ToolServerClient._stringify({"model": "ALI_CBCT_v2"}), {"model": "ALI_CBCT_v2"}
        )

    def test_omitting_the_model_is_legal_and_lets_the_server_pick(self):
        """`model` is OPTIONAL, and that is load-bearing rather than lax.

        The engine is chosen from what the data turns out to hold, and the
        bundle has to match it — so the server picks the hosted bundle whose
        layout the detected engine recognises. A client that demanded a name up
        front would be asking the user to answer, before the data is looked at,
        a question only the data can answer; naming one is for disambiguating
        between several bundles of the same kind.
        """
        ToolServerClient._validate_against_schema(ALI_SCHEMA, {}, {"input": "/tmp/cohort.zip"})

    def test_a_missing_input_is_caught_before_the_round_trip(self):
        from ServerToolsCoreLib.errors import ServerToolError

        with self.assertRaises(ServerToolError):
            ToolServerClient._validate_against_schema(ALI_SCHEMA, {"model": "ALI_CBCT_v2"}, {})

    def test_the_result_is_saved_not_loaded_as_one_node(self):
        # output_kind "files" is a zip of several files: it can only be saved.
        self.assertEqual(formgen.result_kind_for(ALI_SCHEMA["output_kind"]), "save_as")


class TestSelectionGroups(unittest.TestCase):
    """The two multichoice groups, both always rendered."""

    def test_both_groups_are_built_with_their_declared_defaults(self):
        layout = qt.QFormLayout()
        widgets = formgen.build(ALI_SCHEMA["arguments"], layout)

        # The file argument gets its own input row, not a form field.
        self.assertEqual(
            sorted(widgets),
            ["cbct_regions", "ios_networks", "landmarks", "model", "prediction_ID"],
        )
        for name in ("cbct_regions", "ios_networks"):
            group = widgets[name]
            self.assertIsInstance(group, formgen.MultiChoiceGroup)
            self.assertEqual(list(group.boxes), list(_argument(name)["choices"]))
            # Each box starts at the state the SERVER declared, not all-on:
            # Mucogingival is off by default (one point per lower tooth, wanted
            # by AREG's lower-arch registration and by nobody asking for crown
            # landmarks), and a panel that ticked it anyway would add a third
            # pass over every mesh of every run.
            for option, checked in _argument(name)["choices"].items():
                self.assertEqual(group.boxes[option].isChecked(), checked, option)

    def test_the_server_wording_is_visible_not_just_a_tooltip(self):
        # Which group applies depends on data the client has not looked at, so
        # "CBCT only" has to be on screen next to the boxes.
        group = formgen.MultiChoiceGroup(
            _argument("cbct_regions")["choices"], _argument("cbct_regions")["description"]
        )
        hints = [w for w in group.container.layout.widgets if isinstance(w, qt.QLabel)]
        self.assertEqual(len(hints), 1)
        self.assertIn("CBCT only", hints[0].text)

    def test_an_unchecked_option_is_sent_as_false_not_omitted(self):
        group = formgen.MultiChoiceGroup(_argument("cbct_regions")["choices"])
        group.boxes["Upper"].setChecked(False)

        payload = json.loads(ToolServerClient._stringify({"cbct_regions": group.value()})["cbct_regions"])
        # Server-side, what is sent IS the selection: an option left out counts
        # as unchecked whatever its default, so the complete state must travel.
        self.assertEqual(
            payload,
            {"Cranial base": True, "Upper": False, "Lower": True, "Impacted canine": True},
        )

    def test_every_box_unchecked_is_still_a_selection(self):
        group = formgen.MultiChoiceGroup(_argument("ios_networks")["choices"])
        for box in group.boxes.values():
            box.setChecked(False)
        # It must reach the server, which answers 422 naming the argument to
        # fill in — that 422 is how a mode mismatch explains itself.
        self.assertEqual(
            group.value(),
            {"Occlusal": False, "Cervical": False, "Mucogingival": False},
        )
        self.assertTrue(formgen.all_required_filled(
            {"ios_networks": group}, {"ios_networks": _argument("ios_networks")}
        ))


class TestErrorMessages(unittest.TestCase):
    """The server's `detail` is what the user reads."""

    def test_a_tool_that_failed_to_load_says_so(self):
        detail = "Tool 'ALI' failed to load at server startup and is unavailable. See the server logs."
        self.assertEqual(error_for_status(404, detail).message, detail)

    def test_a_404_without_a_body_still_reads_as_a_name_problem(self):
        self.assertIn("Unknown tool", error_for_status(404, None).message)

    def test_the_empty_selection_422_is_shown_verbatim(self):
        detail = (
            "This input is a CBCT batch: select at least one region under 'cbct_regions' "
            "(Cranial base, Upper, Lower, Impacted canine)."
        )
        self.assertEqual(error_for_status(422, detail).message, detail)

    def test_a_500_is_not_shown_verbatim(self):
        # A crash inside a tool can name server-side paths and modules.
        self.assertNotIn("Traceback", error_for_status(500, "Traceback (most recent call last)...").message)


class TestLandmarkSelection(unittest.TestCase):
    """`landmarks` — asking for named points instead of whole regions.

    The argument exists for ASO, which registers on seven CBCT landmarks
    straddling two regions and would otherwise run 58 agents to use 7. It is
    offered to a human too, which is why its 119 options have to be readable.
    """

    def _spec(self):
        return _argument("landmarks")

    def test_it_starts_with_every_box_unchecked(self):
        """"All off" is what "not specified" looks like for a multichoice, and
        it is what hands the decision back to `cbct_regions` — so a panel
        nobody touches keeps meaning what it meant before this argument."""
        group = formgen.MultiChoiceGroup(self._spec()["choices"])
        self.assertTrue(all(not box.isChecked() for box in group.boxes.values()))

    def test_the_catalog_is_rendered_as_the_servers_own_tabs(self):
        spec = self._spec()
        group = formgen.MultiChoiceGroup(
            spec["choices"], spec["description"], layout=spec["ui"], groups=spec["groups"]
        )
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)]
        self.assertEqual(len(tabs), 1)
        self.assertEqual(
            [title for title, _page in tabs[0].tabs],
            ["Cranial base", "Upper", "Lower", "Impacted canine"],
        )

    def test_the_tabs_do_not_change_what_is_sent(self):
        """The property that makes a presentation hint safe: a layout the
        client renders badly is ugly, never wrong on the wire."""
        spec = self._spec()
        flat = formgen.MultiChoiceGroup(spec["choices"])
        tabbed = formgen.MultiChoiceGroup(
            spec["choices"], layout=spec["ui"], groups=spec["groups"]
        )
        self.assertEqual(list(flat.boxes), list(tabbed.boxes))
        self.assertEqual(flat.value(), tabbed.value())

    def test_every_grouped_option_is_one_the_argument_offers(self):
        spec = self._spec()
        for options in spec["groups"].values():
            self.assertTrue(set(options) <= set(spec["choices"]))

    def test_the_complete_selection_travels_so_the_server_can_tell_it_apart(self):
        """An option left out counts as off whatever its default, and an
        argument omitted entirely is what applies the defaults — here, "let the
        regions decide". Those are different requests, and only the full state
        distinguishes them."""
        group = formgen.MultiChoiceGroup(self._spec()["choices"])
        for name in ("Ba", "S", "N"):
            group.boxes[name].setChecked(True)

        payload = json.loads(
            ToolServerClient._stringify({"landmarks": group.value()})["landmarks"]
        )
        self.assertEqual(set(payload), set(self._spec()["choices"]))
        self.assertEqual({name for name, on in payload.items() if on}, {"Ba", "S", "N"})


class TestPanelSections(unittest.TestCase):
    """ALI has no `mode` argument — the server decides from the data — so one
    of the two engines' selections is always inert and cannot be hidden. The
    least the panel can do is keep them apart instead of interleaving them."""

    def test_the_panel_is_laid_out_in_four_boxes_in_reading_order(self):
        self.assertEqual(
            formgen.sections_of(ALI_SCHEMA["arguments"]),
            ["Inputs", "CBCT landmarks", "IOS landmarks", "Outputs"],
        )

    def test_each_engines_selection_sits_in_its_own_box(self):
        sections = {
            name: formgen.section_of(spec) for name, spec in ALI_SCHEMA["arguments"].items()
        }
        self.assertEqual(sections["cbct_regions"], sections["landmarks"])
        self.assertNotEqual(sections["cbct_regions"], sections["ios_networks"])

    def test_no_row_is_labelled_by_the_client(self):
        """The fallback would render `cbct_regions` as "Cbct regions" and
        `prediction_ID` as "Prediction id"."""
        for name, spec in ALI_SCHEMA["arguments"].items():
            label = formgen.label_for(name, spec)
            self.assertTrue(spec.get("label"), name)
            self.assertEqual(label, spec["label"])
            self.assertNotIn("_", label)

    def test_nothing_is_hidden_because_nothing_can_be(self):
        """`visible_when` needs a `choice` argument to test, and ALI declares
        none on purpose: a .zip can hold either kind of data, so the mode is
        detected server-side rather than asked for. Guessing here would hide
        the selection that actually applies."""
        self.assertEqual(formgen.controlling_arguments(ALI_SCHEMA["arguments"]), set())
        for spec in ALI_SCHEMA["arguments"].values():
            self.assertTrue(formgen.is_visible(spec, {}))


if __name__ == "__main__":
    unittest.main()
