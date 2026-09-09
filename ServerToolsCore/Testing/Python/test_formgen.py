"""Unit tests for ServerToolsCoreLib.formgen - run outside Slicer, `qt`/`ctk`/
`slicer` stubbed (see qt_stubs.py).

Everything here is driven by EXAMPLE_TOOL_SCHEMA, the real `GET /tools` entry
for `example_tool`: it is the one tool exercising every argument shape the
panel has to render - free text, int, float, a single-choice dropdown, a
multi-choice checkbox group, and a file argument that also accepts a folder.

Usage:
    python3 -m unittest ServerToolsCore/Testing/Python/test_formgen.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import qt_stubs

qt, ctk = qt_stubs.install()

from ServerToolsCoreLib import design, formgen
from ServerToolsCoreLib.client import ToolServerClient

# The server's actual GET /tools payload for example_tool, verbatim.
EXAMPLE_TOOL_SCHEMA = {
    "name": "example_tool",
    "output_kind": "files",
    "arguments": {
        "label": {
            "type": "str", "types": ["str"], "required": True,
            "description": "Free-text label for this run",
            "server_selectable": None, "choices": None, "initial": None,
        },
        "input": {
            "type": "csv_file", "types": ["csv_file", "folder"], "required": True,
            "description": "A single .csv file, or a folder of .csv/.xlsx/.ods files sent as a .zip archive",
            "server_selectable": None, "choices": None, "initial": None,
        },
        "threshold": {
            "type": "float", "types": ["float"], "required": True,
            "description": "Numeric threshold parameter",
            "server_selectable": None, "choices": None, "initial": None,
        },
        # `initial` is null here on purpose: run()'s own default is None
        # ("not specified"), so there is no value to pre-fill. Contrast with
        # PREFILLED_SCHEMA below.
        "iterations": {
            "type": "int", "types": ["int"], "required": False,
            "description": "Optional number of iterations",
            "server_selectable": None, "choices": None, "initial": None,
        },
        "outputs": {
            "type": "multichoice", "types": ["multichoice"], "required": False,
            "description": "Which result files to produce",
            "server_selectable": None, "initial": None,
            "choices": {"summary": True, "preview": True, "columns": False},
        },
        "preview_format": {
            "type": "choice", "types": ["choice"], "required": False,
            "description": "Format of the preview file",
            "server_selectable": None, "initial": None,
            "choices": {"csv": True, "json": False},
        },
    },
}


def _build():
    layout = qt.QFormLayout()
    return formgen.build(EXAMPLE_TOOL_SCHEMA["arguments"], layout), layout


# Scalar arguments whose tool declares an `initial`, the way AMASSS declares
# surface_smoothing=5. Shaped like a real GET /tools payload.
PREFILLED_SCHEMA = {
    "smoothing": {
        "type": "int", "types": ["int"], "required": False,
        "description": "Smoothing iterations", "server_selectable": None,
        "choices": None, "initial": 5,
    },
    "ratio": {
        "type": "float", "types": ["float"], "required": False,
        "description": "A ratio", "server_selectable": None,
        "choices": None, "initial": 0.25,
    },
    "enabled": {
        "type": "bool", "types": ["bool"], "required": False,
        "description": "A flag on by default", "server_selectable": None,
        "choices": None, "initial": True,
    },
    "suffix": {
        "type": "str", "types": ["str"], "required": False,
        "description": "A name suffix", "server_selectable": None,
        "choices": None, "initial": "Pred",
    },
}



class HiddenArgumentTest(unittest.TestCase):
    """`hidden` is never rendered, whatever else the panel holds.

    It carries the arguments a clinician has no business being asked -- which
    CUDA device, nnUNet's tile step size -- named by the deployment rather than
    by the tool. The tool still declares them and still applies its own
    defaults; the client simply does not ask.
    """

    def test_a_hidden_argument_is_not_visible(self):
        self.assertFalse(formgen.is_visible({"type": "float", "hidden": True}, {}))

    def test_hidden_beats_a_satisfied_visible_when(self):
        spec = {"type": "float", "hidden": True, "visible_when": {"mode": "CBCT"}}
        self.assertFalse(formgen.is_visible(spec, {"mode": "CBCT"}))

    def test_an_argument_without_the_key_is_unaffected(self):
        self.assertTrue(formgen.is_visible({"type": "float"}, {}))
        self.assertTrue(formgen.is_visible({"type": "float", "hidden": False}, {}))

class ScalarInitialValueTest(unittest.TestCase):
    """A scalar argument's `initial` reaches its widget.

    This is not cosmetic. collect() sends EVERY widget, so a field the user
    never touched still travels: a spin box left at Qt's own 0 sent 0, and the
    tool's Python default never applied. That is what shipped AMASSS surfaces
    with 0 smoothing iterations while its run() signature read 5.
    """

    def setUp(self):
        self.layout = qt.QFormLayout()
        self.widgets = formgen.build(PREFILLED_SCHEMA, self.layout)

    def test_int_starts_at_the_declared_value(self):
        self.assertEqual(self.widgets["smoothing"].value, 5)

    def test_float_starts_at_the_declared_value(self):
        self.assertAlmostEqual(self.widgets["ratio"].value, 0.25)

    def test_bool_starts_checked_when_declared_true(self):
        self.assertTrue(self.widgets["enabled"].isChecked())

    def test_str_starts_at_the_declared_value(self):
        self.assertEqual(self.widgets["suffix"].text, "Pred")

    def test_collect_returns_the_declared_values_untouched(self):
        """The whole point: an untouched form sends the tool's own defaults."""
        self.assertEqual(
            formgen.collect(self.widgets),
            {"smoothing": 5, "ratio": 0.25, "enabled": True, "suffix": "Pred"},
        )

    def test_absent_initial_leaves_the_qt_default(self):
        """`initial: None` must not be coerced -- iterations means "unset"."""
        layout = qt.QFormLayout()
        widgets = formgen.build(
            {"iterations": EXAMPLE_TOOL_SCHEMA["arguments"]["iterations"]}, layout
        )
        self.assertEqual(widgets["iterations"].value, 0)


class ChoiceWidgetTest(unittest.TestCase):
    """`"choice"`: one QComboBox, items in declaration order, the single true
    entry preselected."""

    def setUp(self):
        self.widgets, self.layout = _build()
        self.combo = self.widgets["preview_format"]

    def test_is_a_combobox(self):
        self.assertIsInstance(self.combo, qt.QComboBox)

    def test_items_are_the_choice_keys_in_declaration_order(self):
        self.assertEqual([self.combo.itemText(i) for i in range(self.combo.count)], ["csv", "json"])

    def test_initial_selection_is_the_true_entry(self):
        self.assertEqual(self.combo.currentText, "csv")
        self.assertEqual(formgen.collect(self.widgets)["preview_format"], "csv")

    def test_reads_back_the_selected_option_name(self):
        self.combo.setCurrentText("json")

        self.assertEqual(formgen.collect(self.widgets)["preview_format"], "json")

    def test_selection_is_sent_in_clear(self):
        self.combo.setCurrentText("json")
        data = ToolServerClient._stringify(formgen.collect(self.widgets))

        self.assertEqual(data["preview_format"], "json")

    def test_true_entry_is_preselected_wherever_it_sits(self):
        # Nothing may assume the default is the first option.
        spec = {"type": "choice", "choices": {"a": False, "b": False, "c": True}}
        widget = formgen._make_widget("k", spec)

        self.assertEqual(widget.currentText, "c")

    def test_description_becomes_the_tooltip(self):
        self.assertEqual(self.combo.toolTip(), "Format of the preview file")


class MultiChoiceWidgetTest(unittest.TestCase):
    """`"multichoice"`: one QCheckBox per option, in declaration order, each
    starting at its declared boolean."""

    def setUp(self):
        self.widgets, self.layout = _build()
        self.group = self.widgets["outputs"]

    def test_is_a_multichoice_group(self):
        self.assertIsInstance(self.group, formgen.MultiChoiceGroup)

    def test_one_checkbox_per_option_in_declaration_order(self):
        self.assertEqual(list(self.group.boxes), ["summary", "preview", "columns"])
        for box in self.group.boxes.values():
            self.assertIsInstance(box, qt.QCheckBox)

    def test_checkboxes_are_laid_out_in_declaration_order(self):
        # The group also lays out the argument's description as a hint label
        # above the boxes, so filter to the boxes themselves.
        laid_out = [w for w in self.group.container.layout.widgets if isinstance(w, qt.QCheckBox)]
        self.assertEqual([box.text for box in laid_out], ["summary", "preview", "columns"])

    def test_the_description_is_laid_out_above_the_boxes(self):
        laid_out = self.group.container.layout.widgets
        self.assertIsInstance(laid_out[0], qt.QLabel)
        self.assertEqual(laid_out[0].text, "Which result files to produce")

    def test_initial_state_matches_the_declared_booleans(self):
        self.assertEqual(
            [self.group.boxes[option].isChecked() for option in ("summary", "preview", "columns")],
            [True, True, False],
        )

    def test_reads_back_the_full_state_after_toggling(self):
        self.group.boxes["preview"].setChecked(False)
        self.group.boxes["columns"].setChecked(True)

        self.assertEqual(
            formgen.collect(self.widgets)["outputs"],
            {"summary": True, "preview": False, "columns": True},
        )

    def test_every_option_is_reported_even_when_none_is_checked(self):
        # Server-side, what is sent *is* the selection: an option left out of
        # the payload counts as unchecked whatever its declared default, and
        # omitting the argument entirely is what applies the defaults. So
        # "everything unchecked" has to be spelled out in full, not dropped.
        for box in self.group.boxes.values():
            box.setChecked(False)

        self.assertEqual(
            formgen.collect(self.widgets)["outputs"],
            {"summary": False, "preview": False, "columns": False},
        )

    def test_description_becomes_the_tooltip(self):
        self.assertEqual(self.group.container.toolTip(), "Which result files to produce")


class MultiChoiceEncodingTest(unittest.TestCase):
    """How the checkbox state reaches the wire (client._stringify)."""

    def setUp(self):
        self.widgets, _ = _build()
        self.group = self.widgets["outputs"]

    def test_sent_as_json(self):
        self.group.boxes["preview"].setChecked(False)
        self.group.boxes["columns"].setChecked(True)

        data = ToolServerClient._stringify(formgen.collect(self.widgets))

        self.assertEqual(
            json.loads(data["outputs"]),
            {"summary": True, "preview": False, "columns": True},
        )

    def test_json_uses_lowercase_booleans_not_python_repr(self):
        data = ToolServerClient._stringify(formgen.collect(self.widgets))

        self.assertIn('"summary": true', data["outputs"])
        self.assertNotIn("True", data["outputs"])

    def test_all_unchecked_is_not_an_empty_field(self):
        for box in self.group.boxes.values():
            box.setChecked(False)

        data = ToolServerClient._stringify(formgen.collect(self.widgets))

        self.assertEqual(json.loads(data["outputs"]), {"summary": False, "preview": False, "columns": False})

    def test_option_name_containing_a_comma_survives(self):
        # Why JSON and not the server's `a,b` shortcut: that spelling is for
        # curl and cannot represent an option name with a comma in it.
        group = formgen.MultiChoiceGroup({"a,b": True, "c": False})

        payload = ToolServerClient._stringify({"outputs": group.value()})["outputs"]

        self.assertEqual(json.loads(payload), {"a,b": True, "c": False})


class OtherArgumentTypesTest(unittest.TestCase):
    """`choices: null` arguments must produce neither of the two new widgets."""

    def setUp(self):
        self.widgets, self.layout = _build()

    def test_str_int_float_are_unchanged(self):
        self.assertIsInstance(self.widgets["label"], qt.QLineEdit)
        self.assertIsInstance(self.widgets["threshold"], qt.QDoubleSpinBox)
        self.assertIsInstance(self.widgets["iterations"], qt.QSpinBox)

    def test_no_choice_widget_for_a_null_choices_argument(self):
        for name in ("label", "threshold", "iterations"):
            widget = self.widgets[name]
            self.assertNotIsInstance(widget, qt.QComboBox, name)
            self.assertNotIsInstance(widget, qt.QCheckBox, name)
            self.assertNotIsInstance(widget, formgen.MultiChoiceGroup, name)

    def test_file_argument_is_not_a_generated_field(self):
        # "input" is provided by base_widget from FILE_INPUTS, not by build().
        self.assertNotIn("input", self.widgets)

    def test_every_other_argument_gets_exactly_one_row(self):
        self.assertEqual(
            [name for name, _spec in EXAMPLE_TOOL_SCHEMA["arguments"].items() if name != "input"],
            list(self.widgets),
        )
        self.assertEqual(len(self.layout.rows), len(self.widgets))

    def test_collect_returns_one_entry_per_generated_field(self):
        collected = formgen.collect(self.widgets)

        self.assertEqual(set(collected), set(self.widgets))
        self.assertEqual(collected["label"], "")
        self.assertEqual(collected["threshold"], 0.0)

    def test_required_flag_drives_the_apply_button(self):
        arguments = EXAMPLE_TOOL_SCHEMA["arguments"]
        self.assertFalse(formgen.all_required_filled(self.widgets, arguments))  # "label" still empty

        self.widgets["label"].setText("run-1")

        self.assertTrue(formgen.all_required_filled(self.widgets, arguments))


class MultiChoiceRequiredTest(unittest.TestCase):
    def test_an_all_unchecked_required_multichoice_still_counts_as_filled(self):
        schema = {"outputs": {"type": "multichoice", "required": True, "choices": {"a": False, "b": False}}}
        layout = qt.QFormLayout()
        widgets = formgen.build(schema, layout)

        self.assertTrue(formgen.all_required_filled(widgets, schema))


class ChangeSignalTest(unittest.TestCase):
    def setUp(self):
        self.widgets, _ = _build()
        self.calls = []

    def test_toggling_any_checkbox_notifies(self):
        formgen.connect_changed(self.widgets["outputs"], lambda *_a: self.calls.append(1))

        self.widgets["outputs"].boxes["columns"].setChecked(True)

        self.assertEqual(len(self.calls), 1)

    def test_changing_the_choice_notifies(self):
        formgen.connect_changed(self.widgets["preview_format"], lambda *_a: self.calls.append(1))

        self.widgets["preview_format"].setCurrentText("json")

        self.assertEqual(len(self.calls), 1)


class AutoFileModeTest(unittest.TestCase):
    """The one rule deciding what a file argument's picker looks like - and,
    downstream, whether base_widget zips the selection before uploading."""

    def test_folder_plus_a_file_type_offers_both(self):
        self.assertEqual(formgen.auto_file_mode(EXAMPLE_TOOL_SCHEMA["arguments"]["input"]), "file_or_folder")

    def test_no_folder_means_a_single_file(self):
        self.assertEqual(formgen.auto_file_mode({"types": ["nifti_file"]}), "single_file")

    def test_folder_only_means_a_zipped_folder(self):
        self.assertEqual(formgen.auto_file_mode({"types": ["folder"]}), "folder_zip")

    def test_an_unknown_argument_falls_back_to_a_single_file(self):
        # What base_widget resolves against when the schema could not be loaded.
        self.assertEqual(formgen.auto_file_mode({}), "single_file")


class FileInputModesTest(unittest.TestCase):
    """Which arguments get an input row, and with which picker, is the
    schema's answer; a module's FILE_INPUTS only overrides it."""

    ARGUMENTS = EXAMPLE_TOOL_SCHEMA["arguments"]

    def test_every_file_argument_is_offered_without_being_declared(self):
        self.assertEqual(formgen.file_input_modes(self.ARGUMENTS), {"input": "file_or_folder"})

    def test_scalar_arguments_are_not_file_inputs(self):
        modes = formgen.file_input_modes(self.ARGUMENTS)

        for name in ("label", "threshold", "iterations", "outputs", "preview_format"):
            self.assertNotIn(name, modes)

    def test_declaring_auto_changes_nothing(self):
        # What ExampleTool used to spell out by hand.
        self.assertEqual(
            formgen.file_input_modes(self.ARGUMENTS, {"input": "auto"}),
            formgen.file_input_modes(self.ARGUMENTS),
        )

    def test_an_override_wins_over_the_derived_mode(self):
        # SurgMovPred: the schema types "input" as a zip_file, the module wants
        # to hand the user a folder picker and zip it client-side.
        arguments = {"input": {"type": "zip_file", "types": ["zip_file"], "required": True}}

        self.assertEqual(formgen.file_input_modes(arguments), {"input": "single_file"})
        self.assertEqual(
            formgen.file_input_modes(arguments, {"input": "folder_zip"}), {"input": "folder_zip"}
        )

    def test_a_scene_node_input_can_only_come_from_an_override(self):
        # AMASSS: the server declares a nifti_file; that it is filled from a
        # MRML volume node is knowledge the server does not have.
        arguments = {"file": {"type": "nifti_file", "types": ["nifti_file"], "required": True}}

        self.assertEqual(
            formgen.file_input_modes(arguments, {"file": "volume_node"}), {"file": "volume_node"}
        )

    def test_none_leaves_an_optional_file_argument_out(self):
        arguments = {
            "input": {"type": "csv_file", "types": ["csv_file"], "required": True},
            "attachment": {"type": "file", "types": ["file"], "required": False},
        }

        self.assertEqual(
            formgen.file_input_modes(arguments, {"attachment": "none"}), {"input": "single_file"}
        )

    def test_rows_follow_schema_order_even_when_overridden(self):
        arguments = {
            "first": {"type": "csv_file", "types": ["csv_file"]},
            "second": {"type": "zip_file", "types": ["zip_file"]},
        }

        modes = formgen.file_input_modes(arguments, {"first": "folder_zip"})

        self.assertEqual(list(modes), ["first", "second"])

    def test_no_schema_yields_no_inputs(self):
        self.assertEqual(formgen.file_input_modes({}), {})


class ResultKindTest(unittest.TestCase):
    """RESULT_KIND is derived from the tool's output_kind, except where the
    server genuinely cannot answer."""

    def test_derived_from_output_kind(self):
        self.assertEqual(formgen.result_kind_for("text"), "text")
        self.assertEqual(formgen.result_kind_for("segmentation"), "segmentation")
        self.assertEqual(formgen.result_kind_for("files"), "save_as")

    def test_example_tool_needs_no_declaration(self):
        self.assertEqual(formgen.result_kind_for(EXAMPLE_TOOL_SCHEMA["output_kind"]), "save_as")

    def test_a_single_file_defaults_to_saving_it(self):
        # "file" is the ambiguous one: the server says a file comes back, not
        # whether it is a volume, a mesh, or something to write to disk.
        self.assertEqual(formgen.result_kind_for("file"), "save_as")

    def test_a_declaration_always_wins(self):
        self.assertEqual(formgen.result_kind_for("file", "volume"), "volume")
        self.assertEqual(formgen.result_kind_for("files", "text"), "text")

    def test_an_unknown_or_missing_output_kind_falls_back_to_text(self):
        # Also the no-schema case (an unreachable server).
        self.assertEqual(formgen.result_kind_for(None), "text")
        self.assertEqual(formgen.result_kind_for("something_new"), "text")


class FileOrFolderInputTest(unittest.TestCase):
    """The `input` argument: `types` = ["csv_file", "folder"] - one path field
    taking either, with the client working out which it got."""

    def setUp(self):
        self.spec = EXAMPLE_TOOL_SCHEMA["arguments"]["input"]
        self.field = formgen.file_widget(self.spec)
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)
        self.csv = os.path.join(self.work, "measurements.csv")
        with open(self.csv, "w") as fh:
            fh.write("a,b\n1,2\n")
        self.folder = os.path.join(self.work, "cohort")
        os.makedirs(self.folder)

    def test_folder_in_types_gives_a_single_field_accepting_both(self):
        self.assertIsInstance(self.field, formgen.FileOrFolderInput)
        self.assertIsInstance(self.field.pathEdit, qt.QLineEdit)

    def test_the_file_dialog_is_restricted_to_the_declared_extensions(self):
        # The extensions still come from `types` - that is the whole point of
        # driving the dialog here rather than letting ctkPathLineEdit do it.
        self.field.fileButton.clicked.emit()

        self.assertIn("*.csv", qt.QFileDialog.last_open_file_args[3])

    def test_nothing_selected_is_not_a_folder(self):
        self.assertEqual(self.field.currentPath, "")
        self.assertFalse(self.field.is_folder())

    def test_a_file_is_detected_as_a_file(self):
        self.field.pathEdit.setText(self.csv)

        self.assertEqual(self.field.currentPath, self.csv)
        self.assertFalse(self.field.is_folder())

    def test_a_folder_is_detected_as_a_folder(self):
        # The user says nothing: picking the path *is* saying which it is.
        self.field.pathEdit.setText(self.folder)

        self.assertTrue(self.field.is_folder())

    def test_a_folder_pasted_into_the_field_is_still_detected(self):
        # A kind selector made this a wrong request: a folder left under
        # "File" was uploaded as a file and failed at open().
        self.field.pathEdit.setText(self.csv)
        self.assertFalse(self.field.is_folder())

        self.field.pathEdit.setText(self.folder)

        self.assertTrue(self.field.is_folder())

    def test_surrounding_whitespace_is_ignored(self):
        # A pasted path often carries a trailing newline or space.
        self.field.pathEdit.setText(f"  {self.folder}\n")

        self.assertEqual(self.field.currentPath, self.folder)
        self.assertTrue(self.field.is_folder())

    def test_a_nonexistent_path_is_not_taken_for_a_folder(self):
        self.field.pathEdit.setText(os.path.join(self.work, "gone"))

        self.assertFalse(self.field.is_folder())

    def test_each_browse_button_fills_the_same_field(self):
        qt.QFileDialog.next_directory = self.folder
        qt.QFileDialog.next_file = self.csv
        self.addCleanup(setattr, qt.QFileDialog, "next_directory", "")
        self.addCleanup(setattr, qt.QFileDialog, "next_file", "")

        self.field.folderButton.clicked.emit()
        self.assertEqual(self.field.currentPath, self.folder)
        self.assertTrue(self.field.is_folder())

        self.field.fileButton.clicked.emit()
        self.assertEqual(self.field.currentPath, self.csv)
        self.assertFalse(self.field.is_folder())

    def test_a_cancelled_dialog_keeps_the_current_selection(self):
        self.field.pathEdit.setText(self.csv)
        qt.QFileDialog.next_directory = ""  # what Qt returns when cancelled
        qt.QFileDialog.next_file = ""

        self.field.folderButton.clicked.emit()
        self.field.fileButton.clicked.emit()

        self.assertEqual(self.field.currentPath, self.csv)

    def test_every_selection_notifies_whatever_its_kind(self):
        # Regression: on a ctkPathLineEdit, a *.csv name filter silences
        # currentPathChanged for every folder (and every non-matching file),
        # so the Apply button would never enable after choosing a folder.
        calls = []
        formgen.connect_changed(self.field, lambda *_a: calls.append(1))
        qt.QFileDialog.next_directory = self.folder
        self.addCleanup(setattr, qt.QFileDialog, "next_directory", "")

        self.field.pathEdit.setText(self.csv)
        self.field.folderButton.clicked.emit()
        self.field.pathEdit.setText(os.path.join(self.work, "other.xlsx"))

        self.assertEqual(len(calls), 3)

    def test_an_explicit_mode_overrides_the_schema_rule(self):
        # SurgMovPred's "input" is typed zip_file and the module still wants a
        # folder picker (it zips it): a declared mode wins over the derived one.
        field = formgen.file_widget({"type": "zip_file", "types": ["zip_file"]}, "folder_zip")

        self.assertIsInstance(field, ctk.ctkPathLineEdit)
        self.assertEqual(field.filters, ctk.ctkPathLineEdit.Dirs)

    def test_a_volume_argument_gets_the_sources_dropdown_around_its_picker(self):
        field = formgen.file_widget({"type": "nifti_file", "types": ["nifti_file"]})

        # accepts_volume: a scan input can also be satisfied by a volume open
        # in the scene, so its picker comes wrapped in the sources dropdown.
        self.assertIsInstance(field, formgen.ServerFileInput)
        self.assertIsInstance(field.local, ctk.ctkPathLineEdit)
        self.assertEqual(field.local.filters, ctk.ctkPathLineEdit.Files)
        self.assertIn("Supported files (*.nii *.nii.gz)", field.local.nameFilters)

    def test_a_non_volume_file_argument_gets_a_plain_file_picker(self):
        field = formgen.file_widget({"type": "csv_file", "types": ["csv_file"]})

        self.assertIsInstance(field, ctk.ctkPathLineEdit)
        self.assertEqual(field.filters, ctk.ctkPathLineEdit.Files)

    def test_a_generic_file_argument_is_left_unrestricted(self):
        field = formgen.file_widget({"type": "file", "types": ["file"]})

        self.assertEqual(field.nameFilters, [])
        self.assertEqual(field.nameFilterAssignments, 0)

    def test_single_kind_pickers_are_configured_once_and_never_touched(self):
        # Regression: re-assigning nameFilters on a live ctkPathLineEdit
        # corrupts it and crashes Slicer, either on the next filters
        # assignment or later at teardown. Every ctkPathLineEdit this module
        # hands out must come fully configured and stay that way.
        for spec, mode in (
            ({"types": ["csv_file"]}, "single_file"),
            ({"types": ["zip_file"]}, "folder_zip"),
            ({"types": ["file"]}, "single_file"),
        ):
            picker = formgen.file_widget(spec, mode)
            self.assertEqual(picker.filterAssignments, 1, mode)
            self.assertLessEqual(picker.nameFilterAssignments, 1, mode)


# ---------------------------------------------------------------------------
# Presentation hints (ArgSpec.section / visible_when / ui / groups)
# ---------------------------------------------------------------------------

def _multichoice(choices, **hints):
    spec = {
        "type": "multichoice", "types": ["multichoice"], "required": False,
        "description": "", "server_selectable": None, "choices": choices,
        "initial": None,
    }
    spec.update(hints)
    return spec


# Four options, two groups, one option left out of every group on purpose.
_LAYOUT_CHOICES = {"a": True, "b": False, "c": True, "d": False}
_LAYOUT_GROUPS = {"First": ["a", "b"], "Second": ["c"]}


class MultiChoiceLayoutTest(unittest.TestCase):
    """The four layouts must be indistinguishable from the outside.

    This is the property that makes a presentation hint safe: a layout the
    client renders badly is ugly, never wrong on the wire. Everything
    downstream (collect, connect_changed, all_required_filled, and the JSON
    client.py builds) reads `boxes` and `value()`, so those must not depend on
    where the boxes were put.
    """

    def _group(self, layout, groups=None):
        return formgen.MultiChoiceGroup(_LAYOUT_CHOICES, "", layout=layout, groups=groups)

    def test_every_layout_reads_back_identically(self):
        for layout, groups in ((None, None), ("inline", None),
                               ("grid", _LAYOUT_GROUPS), ("tabs", _LAYOUT_GROUPS)):
            group = self._group(layout, groups)
            self.assertEqual(list(group.boxes), list(_LAYOUT_CHOICES), layout)
            self.assertEqual(group.value(), _LAYOUT_CHOICES, layout)

    def test_a_grouped_layout_keeps_declaration_order_not_group_order(self):
        # "Second" holds "c", declared third; the read-back order stays the
        # schema's, because that is the order the server matches against.
        group = self._group("tabs", {"Second": ["c"], "First": ["a", "b"]})
        self.assertEqual(list(group.boxes), ["a", "b", "c", "d"])

    def test_an_option_left_out_of_every_group_is_still_offered(self):
        # The server rejects a group naming an unknown option, but not an
        # option no group mentions. Dropping it would hide a selection the
        # tool genuinely offers.
        group = self._group("tabs", _LAYOUT_GROUPS)
        self.assertIn("d", group.boxes)

    def test_an_unknown_layout_falls_back_to_the_flat_column(self):
        # A presentation hint from a newer server must never break an older
        # client: the panel still renders, just plainly.
        group = self._group("carousel")
        self.assertEqual(group.value(), _LAYOUT_CHOICES)

    def test_tabs_makes_one_tab_per_group(self):
        group = self._group("tabs", _LAYOUT_GROUPS)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)]
        self.assertEqual(len(tabs), 1)
        self.assertEqual([title for title, _w in tabs[0].tabs], ["First", "Second", "Other"])

    def test_a_tab_packs_its_options_at_the_top_left(self):
        """Otherwise the grid shares the scroll area's height between its rows.

        Measured in Slicer on ALI's cranial base: eleven 20 px check boxes sat
        94 px apart, three sparse lines floating in a tall empty box, which is
        what "le tableau est moche, pas bien proportionne" describes. Qt has no
        pack flag; a trailing stretched row and column is the idiom.
        """
        group = self._group("tabs", _LAYOUT_GROUPS)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]

        for _title, tab in tabs.tabs:
            # tab -> [scroll area, the group toggle]; the grid is in the area.
            area = tab.layout.widgets[0]
            grid = area.widget.layout
            rows = max((row for row, _column in grid.cells), default=-1) + 1
            self.assertEqual(grid.rowStretch.get(rows), 1,
                             "the spare height must go below the options")
            self.assertEqual(grid.columnStretch.get(formgen._TAB_COLUMNS), 1,
                             "and the spare width to their right")

    def test_the_tab_box_is_bounded_both_ways(self):
        """A minimum alone let the panel's spare vertical space stretch the box
        -- 380 px for ALI's ten cranial landmarks, mostly empty."""
        group = self._group("tabs", _LAYOUT_GROUPS)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]

        self.assertEqual(tabs.minimumHeight(), tabs.maximumHeight())
        self.assertGreaterEqual(tabs.maximumHeight(), design.TABS_MIN_HEIGHT)
        self.assertLessEqual(tabs.maximumHeight(), design.TABS_MAX_HEIGHT)

    def test_the_box_follows_the_tab_on_screen(self):
        """Sizing every tab to the tallest one still left ALI's four-landmark
        tab in a box built for fifty-seven. The box follows the number of boxes,
        which is what the reader is actually looking at."""
        few = ["a"]
        many = ["opt{}".format(i) for i in range(40)]
        choices = {option: False for option in few + many}
        group = formgen.MultiChoiceGroup(
            choices, "", layout="tabs", groups={"Few": few, "Many": many})
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]

        small = tabs.maximumHeight()
        tabs.setCurrentIndex(1)
        large = tabs.maximumHeight()

        self.assertLess(small, large)
        self.assertEqual(small, design.tabs_height_for(1))

    def test_the_height_comes_back_when_the_small_tab_does(self):
        few, many = ["a"], ["opt{}".format(i) for i in range(40)]
        group = formgen.MultiChoiceGroup(
            {option: False for option in few + many}, "",
            layout="tabs", groups={"Few": few, "Many": many})
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]

        tabs.setCurrentIndex(1)
        tabs.setCurrentIndex(0)

        self.assertEqual(tabs.maximumHeight(), design.tabs_height_for(1))

    def test_a_long_catalogue_is_capped_rather_than_pushing_apply_off_screen(self):
        many = {"opt{}".format(i): False for i in range(200)}
        group = formgen.MultiChoiceGroup(many, "", layout="tabs", groups=None)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]

        self.assertEqual(tabs.maximumHeight(), design.TABS_MAX_HEIGHT)

    def test_a_short_catalogue_no_longer_gets_a_tall_empty_box(self):
        """The floor used to be 220 px whatever the content held."""
        few = {"a": False, "b": True}
        group = formgen.MultiChoiceGroup(few, "", layout="tabs", groups=None)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]

        self.assertEqual(tabs.maximumHeight(), design.tabs_height_for(1))
        self.assertLess(tabs.maximumHeight(), 220)

    def _buttons_of(self, tabs, index):
        """tab -> [scroll area, the button pair]. {label: button}."""
        tab = tabs.tabs[index][1]
        bar = tab.layout.widgets[1]
        return {button.text: button for button in bar.layout.widgets}

    def test_a_tab_can_be_taken_in_one_click(self):
        """The original extension's `Switch group selection`, restored.

        Dropping it made a ten-landmark region cost ten clicks, which is what
        "cocher une region active tous les landmarks de cette region" is asking
        for. Scoped to the tab: the other groups are untouched.
        """
        group = self._group("tabs", _LAYOUT_GROUPS)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]
        first = _LAYOUT_GROUPS["First"]

        # What the OTHER group held before, so the assertion is "untouched"
        # rather than "off" -- `c` is on by default in this catalogue.
        others = {option: group.boxes[option].isChecked()
                  for option in _LAYOUT_GROUPS["Second"]}

        self._buttons_of(tabs, 0)[formgen.SELECT_GROUP_LABEL].clicked.emit()

        for option in first:
            self.assertTrue(group.boxes[option].isChecked(), option)
        for option, before in others.items():
            self.assertEqual(group.boxes[option].isChecked(), before, option)

    def test_deselect_all_clears_only_its_own_tab(self):
        group = self._group("tabs", _LAYOUT_GROUPS)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]
        others = {option: group.boxes[option].isChecked()
                  for option in _LAYOUT_GROUPS["Second"]}

        self._buttons_of(tabs, 0)[formgen.CLEAR_GROUP_LABEL].clicked.emit()

        for option in _LAYOUT_GROUPS["First"]:
            self.assertFalse(group.boxes[option].isChecked(), option)
        for option, before in others.items():
            self.assertEqual(group.boxes[option].isChecked(), before, option)

    def test_each_tab_names_both_actions_rather_than_one_that_changes(self):
        """A single toggle has to say which of the two a click will do, so its
        label moves under the pointer as the group fills. Two named actions are
        always true."""
        group = self._group("tabs", _LAYOUT_GROUPS)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]
        buttons = self._buttons_of(tabs, 0)

        self.assertEqual(set(buttons),
                         {formgen.SELECT_GROUP_LABEL, formgen.CLEAR_GROUP_LABEL})

        buttons[formgen.SELECT_GROUP_LABEL].clicked.emit()

        self.assertEqual(set(self._buttons_of(tabs, 0)), set(buttons),
                         "the labels must not move under the pointer")

    def test_the_group_buttons_change_nothing_on_the_wire(self):
        """Whatever the layout does, `value()` is still the complete state."""
        group = self._group("tabs", _LAYOUT_GROUPS)
        tabs = [w for w in group.container.layout.widgets if isinstance(w, qt.QTabWidget)][0]

        self._buttons_of(tabs, 0)[formgen.SELECT_GROUP_LABEL].clicked.emit()

        self.assertEqual(set(group.value()), set(_LAYOUT_CHOICES))
        self.assertTrue(all(group.value()[option] for option in _LAYOUT_GROUPS["First"]))

    def test_the_chart_stretches_its_rows_but_never_its_columns(self):
        """The columns ARE the arch. Spreading them across whatever width the
        panel happens to have destroys the adjacency the layout exists to show,
        which is why only the rows take the slack here."""
        group = self._group("grid", _LAYOUT_GROUPS)
        area = [w for w in group.container.layout.widgets
                if isinstance(w, qt.QScrollArea)][0]
        grid = area.widget.layout

        self.assertEqual(grid.rowStretch.get(grid.rowCount()), 1)
        self.assertEqual(grid.columnStretch, {})

    def test_a_tabbed_group_has_no_global_bar_beside_its_tab_buttons(self):
        """Every tab already carries a button that takes the whole group; the
        two together were three small links under a button doing the same thing
        one tab at a time."""
        group = self._group("tabs", _LAYOUT_GROUPS)

        texts = [getattr(w, "text", "") for w in group.container.layout.widgets]
        self.assertNotIn(formgen.SELECT_ALL_LABEL, texts)
        self.assertNotIn(formgen.SELECT_DEFAULT_LABEL, texts)

    def test_every_other_layout_keeps_it(self):
        """A flat column of nine structures has no other way to say "all of
        them"."""
        for layout in (None, "inline", "grid"):
            group = self._group(layout, _LAYOUT_GROUPS if layout == "grid" else None)
            bar = group.container.layout.widgets[-1]
            labels = [w.text for w in bar.layout.widgets if hasattr(w, "text")]
            self.assertIn(formgen.SELECT_ALL_LABEL, labels, layout)

    def test_all_none_default_sit_under_the_options_not_at_the_far_edge(self):
        """Right-aligned they floated at the edge of the panel with nothing to
        attach to -- and on ALI the `regions` bar landed directly above the
        LABEL OF THE NEXT FIELD, reading as if it belonged to that one."""
        group = self._group(None)  # flat: still carries the bar
        bar = group.container.layout.widgets[-1]

        labels = [w.text for w in bar.layout.widgets if hasattr(w, "text")]
        self.assertEqual(labels[:3], [formgen.SELECT_ALL_LABEL,
                                      formgen.SELECT_NONE_LABEL,
                                      formgen.SELECT_DEFAULT_LABEL])
        self.assertEqual(bar.layout.stretches, [1],
                         "one trailing stretch, so the links start at the left")

    def test_grid_puts_one_group_per_row_with_its_options_as_columns(self):
        # The chart property: ASO asks for teeth "spread across the arch", and
        # only the positions can show whether a selection is spread.
        group = self._group("grid", _LAYOUT_GROUPS)
        area = group.container.layout.widgets[0]
        grid = area.widget.layout
        self.assertIs(grid.cells[(0, 1)], group.boxes["a"])
        self.assertIs(grid.cells[(0, 2)], group.boxes["b"])
        self.assertIs(grid.cells[(1, 1)], group.boxes["c"])

    def test_select_all_none_and_default(self):
        group = self._group(None)
        group.setAll(True)
        self.assertEqual(set(group.value().values()), {True})
        group.setAll(False)
        self.assertEqual(set(group.value().values()), {False})
        # "Default" restores what the SERVER declared, which is what the old
        # ASO module's per-mode Suggest() button did with a hardcoded list.
        group.restoreDefaults()
        self.assertEqual(group.value(), _LAYOUT_CHOICES)

    def test_a_single_option_argument_gets_no_toolbar(self):
        group = formgen.MultiChoiceGroup({"only": True})
        self.assertEqual(len(group.container.layout.widgets), 1)

    def test_the_layout_reaches_the_widget_through_the_schema(self):
        # Not just constructible by hand: _make_widget has to read `ui`/`groups`
        # off the spec, or a tool declaring them renders flat anyway.
        widgets = formgen.build(
            {"picks": _multichoice(_LAYOUT_CHOICES, ui="tabs", groups=_LAYOUT_GROUPS)},
            qt.QFormLayout(),
        )
        tabs = [w for w in widgets["picks"].container.layout.widgets
                if isinstance(w, qt.QTabWidget)]
        self.assertEqual(len(tabs), 1)


class LabelTest(unittest.TestCase):
    """The words a user reads are the tool's, not this file's."""

    def test_the_declared_label_wins(self):
        self.assertEqual(
            formgen.label_for("input", {"label": "Scan / Landmark Folder"}),
            "Scan / Landmark Folder",
        )

    def test_the_fallback_prettifies_the_argument_name(self):
        self.assertEqual(formgen.label_for("output_suffix", {}), "Output suffix")
        self.assertEqual(formgen.label_for("input", {"label": None}), "Input")
        self.assertEqual(formgen.label_for("input", {"label": "   "}), "Input")

    def test_the_vocabulary_is_spelled_here_because_the_server_may_not_know_it(self):
        """The server hands over "Cbct landmarks" and cannot do better.

        Knowing that CBCT is a word would make it know its tools, which is the
        one thing that server is built not to know -- an executable claim,
        checked on every build. This side IS the dental extension, so the table
        lives here and the word comes out whole.
        """
        self.assertEqual(formgen.label_for("cbct_landmarks", {}), "CBCT landmarks")
        self.assertEqual(formgen.label_for("ios_networks", {}), "IOS networks")
        self.assertEqual(formgen.label_for("prediction_ID", {}), "Prediction ID")

    def test_a_declared_label_gets_the_same_courtesy(self):
        """A tool that typed "Cbct regions" by hand reads the same as one that
        declared nothing: one rule, applied to whatever is displayed."""
        self.assertEqual(
            formgen.label_for("cbct_regions", {"label": "Cbct regions"}),
            "CBCT regions",
        )

    def test_a_phrase_no_rule_could_invent_survives_untouched(self):
        """The case that matters most: no token matches, nothing is rewritten.

        No naming rule can recover "Scan / Landmark Folder" from `input`, which
        is why a tool declares it — and why this pass must never damage one.
        """
        for phrase in ("Scan / Landmark Folder", "Reference (gold) file",
                       "Number of workers", "T1"):
            self.assertEqual(formgen.label_for("whatever", {"label": phrase}), phrase)

    def test_the_pass_matches_whole_words_only(self):
        """"identifier" is not "id", and "action" is not "ct"."""
        self.assertEqual(formgen.label_for("x", {"label": "Patient identifier"}),
                         "Patient identifier")
        self.assertEqual(formgen.label_for("x", {"label": "No action taken"}),
                         "No action taken")

    def test_one_rule_for_generated_fields_and_file_inputs(self):
        # Regression: build() used the raw schema name while base_widget
        # prettified it, so one panel showed "Reference" above "cbct_landmarks".
        schema = {
            "plain": {"type": "str", "types": ["str"], "required": True},
            "named": {"type": "str", "types": ["str"], "label": "A Real Name"},
        }
        layout = qt.QFormLayout()
        formgen.build(schema, layout)
        self.assertEqual([label.text for label, _f in layout.rows], ["Plain *", "A Real Name"])


class SectionTest(unittest.TestCase):
    def test_an_argument_declaring_nothing_lands_in_the_default_section(self):
        self.assertEqual(formgen.section_of({}), formgen.DEFAULT_SECTION)
        self.assertEqual(formgen.section_of({"section": None}), formgen.DEFAULT_SECTION)

    def test_sections_are_ordered_by_first_mention(self):
        schema = {
            "a": {"type": "str", "section": "Setup"},
            "b": {"type": "str"},
            "c": {"type": "str", "section": "Setup"},
            "d": {"type": "str", "section": "Tuning"},
        }
        self.assertEqual(
            formgen.sections_of(schema), ["Setup", formgen.DEFAULT_SECTION, "Tuning"]
        )

    def test_extra_sections_are_appended_unless_already_claimed(self):
        schema = {"a": {"type": "str", "section": "Outputs"}}
        self.assertEqual(formgen.sections_of(schema, ["Outputs", "Extra"]), ["Outputs", "Extra"])

    def test_every_example_tool_argument_keeps_the_single_default_section(self):
        # The compatibility guarantee: a tool declaring no section must render
        # exactly as it did before sections existed.
        self.assertEqual(
            formgen.sections_of(EXAMPLE_TOOL_SCHEMA["arguments"]), [formgen.DEFAULT_SECTION]
        )

    def test_build_routes_each_argument_to_its_section(self):
        schema = {
            "here": {"type": "str", "types": ["str"], "section": "Setup"},
            "there": {"type": "str", "types": ["str"], "section": "Tuning"},
            "nowhere": {"type": "str", "types": ["str"]},
        }
        fallback = qt.QFormLayout()
        sections = {"Setup": qt.QFormLayout(), "Tuning": qt.QFormLayout()}
        rows = {}
        formgen.build(schema, fallback, sections=sections, rows=rows)

        self.assertEqual(len(sections["Setup"].rows), 1)
        self.assertEqual(len(sections["Tuning"].rows), 1)
        # No layout was created for the default section, so it falls back.
        self.assertEqual(len(fallback.rows), 1)
        self.assertEqual(set(rows), {"here", "there", "nowhere"})
        # A row is the pair a caller has to hide together.
        self.assertEqual(len(rows["here"]), 2)


class VisibilityTest(unittest.TestCase):
    def test_an_argument_declaring_nothing_is_always_visible(self):
        self.assertTrue(formgen.is_visible({}, {}))

    def test_every_condition_must_match(self):
        spec = {"visible_when": {"modality": "CBCT", "automation": "Fully-Automated"}}
        self.assertTrue(
            formgen.is_visible(spec, {"modality": "CBCT", "automation": "Fully-Automated"})
        )
        self.assertFalse(
            formgen.is_visible(spec, {"modality": "CBCT", "automation": "Semi-Automated"})
        )
        self.assertFalse(
            formgen.is_visible(spec, {"modality": "IOS", "automation": "Fully-Automated"})
        )

    def test_a_list_of_values_means_any_of_them(self):
        spec = {"visible_when": {"mode": ["a", "b"]}}
        self.assertTrue(formgen.is_visible(spec, {"mode": "b"}))
        self.assertFalse(formgen.is_visible(spec, {"mode": "c"}))

    def test_an_unevaluable_condition_hides_the_field(self):
        # Only reachable when the schema could not be fetched. A field whose
        # precondition is unknown is one the user cannot fill in meaningfully,
        # and hiding it is the answer that cannot produce a wrong request.
        self.assertFalse(formgen.is_visible({"visible_when": {"modality": "CBCT"}}, {}))

    def test_controlling_arguments_are_collected_across_the_schema(self):
        schema = {
            "modality": {"type": "choice"},
            "automation": {"type": "choice"},
            "a": {"type": "str", "visible_when": {"modality": "CBCT"}},
            "b": {"type": "str", "visible_when": {"modality": "IOS", "automation": "Semi"}},
            "c": {"type": "str"},
        }
        self.assertEqual(
            formgen.controlling_arguments(schema), {"modality", "automation"}
        )

    def test_a_hidden_required_field_does_not_block_apply(self):
        schema = {"needed": {"type": "str", "types": ["str"], "required": True,
                             "visible_when": {"mode": "on"}}}
        widgets = formgen.build(schema, qt.QFormLayout())
        self.assertFalse(formgen.all_required_filled(widgets, schema))
        self.assertTrue(formgen.all_required_filled(widgets, schema, hidden={"needed"}))


# ---------------------------------------------------------------------------
# Input sources: the tool's server-hosted test files and the scene's volumes
# ---------------------------------------------------------------------------

_VOLUME_SPEC = {
    "type": "volume_or_zip_file", "types": ["volume_or_zip_file", "folder"],
    "required": True, "description": "", "server_selectable": "testfile",
    "choices": None, "initial": None,
}

# Same shape, but the entries are WEIGHTS: never downloaded, named in the run.
_MODEL_SPEC = dict(_VOLUME_SPEC, server_selectable="model")


class AcceptsVolumeTest(unittest.TestCase):
    """Which file arguments may be satisfied by a volume open in the scene:
    read off the schema, never off a module override."""

    def test_a_volume_type_name_qualifies(self):
        self.assertTrue(formgen.accepts_volume({"types": ["volume_or_zip_file"]}))
        self.assertTrue(formgen.accepts_volume({"types": ["nifti_file"]}))

    def test_a_volume_extension_qualifies(self):
        spec = {"types": ["scan_file"], "extensions": {"scan_file": [".nrrd"]}}
        self.assertTrue(formgen.accepts_volume(spec))

    def test_a_csv_input_never_offers_scene_volumes(self):
        self.assertFalse(formgen.accepts_volume(EXAMPLE_TOOL_SCHEMA["arguments"]["input"]))

    def test_a_surface_only_input_does_not_qualify(self):
        spec = {"types": ["surface_file"], "extensions": {"surface_file": [".vtk", ".stl"]}}
        self.assertFalse(formgen.accepts_volume(spec))


class HumanSizeTest(unittest.TestCase):
    """A test file's size is shown to a clinician deciding whether to click it,
    so it has to read as a size."""

    def test_bytes_megabytes_and_gigabytes(self):
        self.assertEqual(formgen.human_size(2969), "2.9 KB")
        self.assertEqual(formgen.human_size(94 * 1024 * 1024), "94 MB")
        self.assertEqual(formgen.human_size(648 * 1024 * 1024), "648 MB")
        self.assertEqual(formgen.human_size(7 * 1024 * 1024 + 419430), "7.4 MB")
        self.assertEqual(formgen.human_size(3 * 1024 ** 3), "3.0 GB")
        self.assertEqual(formgen.human_size(512), "512 B")

    def test_an_unknown_size_renders_nothing_rather_than_zero(self):
        """A backend that cannot size a tree cheaply sends null, and "0 B"
        would be a claim where the server made none."""
        self.assertEqual(formgen.human_size(None), "")
        self.assertEqual(formgen.human_size(0), "")
        self.assertEqual(formgen.human_size("355640000"), "")


class HostedEntryLabelTest(unittest.TestCase):
    """What one server-hosted test file reads as in the picker."""

    def test_kind_and_size_are_both_shown(self):
        self.assertEqual(
            formgen.hosted_entry_label(
                {"name": "CBCT_FullyAuto", "kind": "folder", "size": 355640000}
            ),
            "CBCT_FullyAuto  (folder, 339 MB)",
        )

    def test_a_known_kind_alone_still_reads(self):
        self.assertEqual(
            formgen.hosted_entry_label({"name": "ROI_box.mrk.json", "kind": "file", "size": None}),
            "ROI_box.mrk.json  (file)",
        )

    def test_an_entry_that_says_nothing_extra_is_just_its_name(self):
        self.assertEqual(
            formgen.hosted_entry_label({"name": "scan.nii.gz", "kind": None, "size": None}),
            "scan.nii.gz",
        )


class InputSourcesTest(unittest.TestCase):
    """The one-line input row: [sources dropdown][local picker], with the
    tool's server-hosted test files above the scene's open volumes.

    There is no "Upload my own file..." entry any more: it was a MODE dressed
    as a file, and what says whether the argument has been given anything is
    the path field.
    """

    def setUp(self):
        self.widget = formgen.file_widget(_VOLUME_SPEC, "file_or_folder")
        self.widget.setChoices([
            {"name": "MG_test_scan.nii.gz", "kind": "file", "size": 94 * 1024 * 1024},
        ])
        self.widget.setVolumeChoices(["CBCT_patient1", "CBCT_patient2"])
        # Real files on disk: the caption reports a size, and a stub size would
        # test the stub.
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, True)

    def test_the_controls_are_one_line_with_the_caption_under_them(self):
        """Two lines, and only two: the controls, then what they hold.

        The controls stayed on one line -- that was the point of the row and
        still is. The caption is a line of its own because it is the only place
        that can name the file and its kind without eliding one of them.
        """
        column = self.widget.container.layout
        self.assertIsInstance(column, qt.QVBoxLayout)
        controls, caption = column.widgets
        self.assertIs(caption, self.widget.caption)
        self.assertIsInstance(controls.layout, qt.QHBoxLayout)
        self.assertIs(controls.layout.widgets[0], self.widget.combo)

    def test_the_caption_is_hidden_until_there_is_something_to_say(self):
        self.assertFalse(self.widget.caption.isVisible())
        self.assertEqual(self.widget.caption.text, "")

    def test_a_downloaded_test_file_says_its_name_and_its_kind(self):
        """The complaint this exists for: the row showed
        `:TestFiles2026-09-09_09+26+53.117/MG_test_scan.nii.gz` and the dropdown
        had gone back to its prompt, so nothing on screen said which file was
        loaded, still less that it was a NIfTI volume."""
        path = os.path.join(self.temp, "MG_test_scan.nii.gz")
        with open(path, "wb") as handle:
            handle.write(b"x" * 2048)

        formgen.set_local_path(self.widget, path)

        caption = self.widget.caption.text
        self.assertIn("MG_test_scan.nii.gz", caption)
        self.assertIn("NIfTI volume", caption)
        self.assertIn("2.0 KB", caption)
        self.assertTrue(self.widget.caption.isVisible())

    def test_it_says_a_fetched_file_is_not_the_user_s_own_copy(self):
        """A download lands in a session folder swept on exit. A user who takes
        it for their own copy will look for it next week and not find it."""
        path = os.path.join(self.temp, "MG_test_scan.nii.gz")
        open(path, "wb").close()

        formgen.set_local_path(self.widget, path)

        self.assertIn("test data", self.widget.caption.text)

    def test_a_file_the_user_chose_themselves_claims_nothing_of_the_sort(self):
        path = os.path.join(self.temp, "my_own_patient.nii.gz")
        open(path, "wb").close()

        formgen.set_local_path(self.widget, path)

        self.assertIn("my_own_patient.nii.gz", self.widget.caption.text)
        self.assertNotIn("test data", self.widget.caption.text)

    def test_a_surface_reads_as_a_surface(self):
        path = os.path.join(self.temp, "T1_01_U_segmented.vtk")
        open(path, "wb").close()

        formgen.set_local_path(self.widget, path)

        self.assertIn("VTK surface", self.widget.caption.text)

    def test_a_name_that_says_nothing_is_not_given_a_kind(self):
        """Better a bare name than a confident guess at what it holds."""
        path = os.path.join(self.temp, "measurements.weird")
        open(path, "wb").close()

        formgen.set_local_path(self.widget, path)

        self.assertIn("measurements.weird", self.widget.caption.text)
        self.assertNotIn(" - ", self.widget.caption.text.replace(
            "measurements.weird", ""))

    def test_the_full_path_stays_reachable_as_a_tooltip(self):
        """The caption names the file; the tooltip says where it sits. Neither
        costs a line the panel does not have."""
        path = os.path.join(self.temp, "MG_test_scan.nii.gz")
        open(path, "wb").close()

        formgen.set_local_path(self.widget, path)

        self.assertEqual(self.widget.local.pathEdit.toolTip(), path)

    def test_an_open_volume_says_it_is_one(self):
        """Nothing is on disk for it, so `describe_file` has nothing to read --
        and "no file chosen" would be a lie about a satisfied argument."""
        self.widget.combo.setCurrentIndex(2)

        self.assertIn("Open volume", self.widget.caption.text)
        self.assertIn("CBCT_patient1", self.widget.caption.text)

    def test_the_caption_empties_when_the_input_does(self):
        path = os.path.join(self.temp, "MG_test_scan.nii.gz")
        open(path, "wb").close()
        formgen.set_local_path(self.widget, path)

        formgen.set_local_path(self.widget, "")

        self.assertEqual(self.widget.caption.text, "")
        self.assertFalse(self.widget.caption.isVisible())

    def test_entries_are_the_prompt_then_test_files_then_volumes(self):
        combo = self.widget.combo
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count)],
            [
                formgen.ServerFileInput.PROMPT_BOTH,
                "MG_test_scan.nii.gz  (file, 94 MB)",
                formgen.OPEN_VOLUME_PREFIX + "CBCT_patient1",
                formgen.OPEN_VOLUME_PREFIX + "CBCT_patient2",
            ],
        )

    def test_the_prompt_names_what_the_list_holds(self):
        """It used to be the path field's own placeholder, word for word.

        Photographed, the panel showed "Select a file or a folder" twice side by
        side, and the dropdown read as a duplicate of the field beside it rather
        than as the one place a tool's test data is reached from. Nobody opens a
        control that appears to repeat its neighbour.
        """
        self.assertEqual(self.widget.combo.itemText(0),
                         formgen.ServerFileInput.PROMPT_BOTH)
        self.assertNotEqual(self.widget.combo.itemText(0),
                            self.widget.local.pathEdit.placeholderText)

    def test_the_prompt_offers_only_what_is_there(self):
        """Naming a source the list does not have would be worse than saying
        nothing: a user opens it, finds no test data, and stops trusting it."""
        self.widget.setVolumeChoices([])
        self.assertEqual(self.widget.combo.itemText(0),
                         formgen.ServerFileInput.PROMPT_HOSTED)

        self.widget.setChoices([])
        self.widget.setVolumeChoices(["CBCT_patient1"])
        self.assertEqual(self.widget.combo.itemText(0),
                         formgen.ServerFileInput.PROMPT_VOLUMES)

    def test_an_empty_list_keeps_the_neutral_words(self):
        self.widget.setChoices([])
        self.widget.setVolumeChoices([])
        self.assertEqual(self.widget.combo.itemText(0),
                         formgen.ServerFileInput.CHOOSE_OPTION)

    def test_a_model_row_says_model_because_nothing_is_fetched(self):
        """Those entries are not test data: they are the value that travels,
        and the weights never leave the server."""
        widget = formgen.file_widget(_MODEL_SPEC, "single_file")
        widget.setChoices([{"name": "AMASSS_Models", "kind": "folder", "size": None}])

        self.assertEqual(widget.combo.itemText(0),
                         formgen.ServerFileInput.PROMPT_MODEL)

    def test_the_prompt_is_also_the_collapsed_box_tooltip(self):
        """The box stays narrow on purpose, so the prompt is the first thing
        elided -- the tooltip is where it survives."""
        self.assertEqual(self.widget.combo.toolTip(),
                         formgen.ServerFileInput.PROMPT_BOTH)

    def test_the_default_state_names_nothing(self):
        self.assertEqual(self.widget.hosted_name(), "")
        self.assertEqual(self.widget.volume_name(), "")
        self.assertEqual(self.widget.currentPath, "")

    def test_choosing_a_test_file_hands_its_name_to_the_callback(self):
        """formgen never talks HTTP: it reports the pick and base_widget
        downloads it."""
        picked = []
        self.widget.setHostedCallback(picked.append)

        self.widget.combo.setCurrentIndex(1)

        self.assertEqual(picked, ["MG_test_scan.nii.gz"])

    def test_a_test_file_pick_clears_a_previously_chosen_path(self):
        """Cleared when the pick starts, not when the download lands: a run
        launched mid-download must not send the file it replaced."""
        self.widget.local.pathEdit.setText("/data/my_own_scan.nii.gz")

        self.widget.combo.setCurrentIndex(1)

        self.assertEqual(self.widget.currentPath, "")

    def test_the_downloaded_path_becomes_an_ordinary_local_selection(self):
        self.widget.setHostedCallback(
            lambda name: formgen.set_local_path(self.widget, "/tmp/session/" + name)
        )

        self.widget.combo.setCurrentIndex(1)

        self.assertEqual(self.widget.currentPath, "/tmp/session/MG_test_scan.nii.gz")
        # The dropdown is an action list, not a mode: once the file is on disk
        # the path field is the whole of the state.
        self.assertEqual(self.widget.combo.currentIndex, 0)
        self.assertEqual(self.widget.hosted_name(), "")

    def test_choosing_a_volume_is_not_a_test_file_selection(self):
        self.widget.combo.setCurrentIndex(2)

        self.assertEqual(self.widget.volume_name(), "CBCT_patient1")
        self.assertEqual(self.widget.hosted_name(), "")
        # Nothing to read off disk either: the node is exported at upload time.
        self.assertEqual(self.widget.currentPath, "")

    def test_choosing_a_volume_clears_the_local_path(self):
        self.widget.local.pathEdit.setText("/data/scan.nii.gz")

        self.widget.combo.setCurrentIndex(3)

        self.assertEqual(self.widget.local.currentPath, "")

    def test_typing_a_local_path_resets_the_dropdown(self):
        self.widget.combo.setCurrentIndex(2)

        self.widget.local.pathEdit.setText("/data/scan.nii.gz")

        self.assertEqual(self.widget.volume_name(), "")
        self.assertEqual(self.widget.currentPath, "/data/scan.nii.gz")

    def test_a_chosen_volume_survives_a_test_file_list_refresh(self):
        self.widget.combo.setCurrentIndex(2)

        self.widget.setChoices([
            {"name": "MG_test_scan.nii.gz", "kind": "file", "size": 94 * 1024 * 1024},
            {"name": "cohort_10_patients.zip", "kind": "file", "size": None},
        ])

        self.assertEqual(self.widget.volume_name(), "CBCT_patient1")

    def test_a_gone_volume_falls_back_to_the_prompt(self):
        self.widget.combo.setCurrentIndex(2)

        self.widget.setVolumeChoices([])

        self.assertEqual(self.widget.volume_name(), "")
        # The prompt follows what is left in the list: the test files.
        self.assertEqual(self.widget.combo.currentText,
                         formgen.ServerFileInput.PROMPT_HOSTED)

    def test_a_refresh_starts_no_download_of_its_own(self):
        """clear()+addItems reselects index 0 and would otherwise fire the
        selection handler for a choice nobody made."""
        picked = []
        self.widget.setHostedCallback(picked.append)

        self.widget.setChoices([{"name": "MG_test_scan.nii.gz", "kind": "file", "size": 1}])
        self.widget.setVolumeChoices(["CBCT_patient1"])

        self.assertEqual(picked, [])

    def test_a_test_file_named_like_a_volume_entry_is_not_misread(self):
        # Selection kind is decided by index, so even a hosted file named
        # like a volume entry stays a hosted selection.
        tricky = formgen.OPEN_VOLUME_PREFIX + "CBCT_patient1"
        self.widget.setChoices([{"name": tricky, "kind": None, "size": None}])

        self.widget.combo.setCurrentIndex(1)

        self.assertEqual(self.widget.hosted_name(), tricky)
        self.assertEqual(self.widget.volume_name(), "")

    def test_bare_names_are_accepted_as_entries(self):
        """A caller holding only names -- an older server publishes no
        `entries` at all -- still gets a working list."""
        self.widget.setChoices(["a.nii.gz", "b.nii.gz"])

        self.assertEqual(self.widget.combo.itemText(1), "a.nii.gz")
        self.assertEqual(
            self.widget.hosted_entries(),
            [{"name": "a.nii.gz", "kind": None, "size": None},
             {"name": "b.nii.gz", "kind": None, "size": None}],
        )


class SetLocalPathTest(unittest.TestCase):
    """Writing a downloaded path into an input row, whichever shape it has."""

    def test_set_local_path_reaches_the_path_field_of_either_shape(self):
        wrapped = formgen.file_widget(_VOLUME_SPEC, "file_or_folder")
        bare = formgen.file_widget(EXAMPLE_TOOL_SCHEMA["arguments"]["input"], "file_or_folder")

        formgen.set_local_path(wrapped, "/downloads/scan.nii.gz")
        formgen.set_local_path(bare, "/downloads/cohort")

        self.assertEqual(wrapped.currentPath, "/downloads/scan.nii.gz")
        self.assertEqual(bare.currentPath, "/downloads/cohort")

    def test_an_argument_with_no_extra_source_is_a_bare_picker(self):
        """No dropdown at all where there is nothing to put in it: example_tool's
        csv input is neither server_selectable nor volume-ish."""
        bare = formgen.file_widget(EXAMPLE_TOOL_SCHEMA["arguments"]["input"], "file_or_folder")
        self.assertIsInstance(bare, formgen.FileOrFolderInput)
        self.assertFalse(hasattr(bare, "combo"))


# ---------------------------------------------------------------------------
# Sliders (ArgSpec.ui = "slider" on int/float) and bounds on spin boxes
# ---------------------------------------------------------------------------

def _numeric(arg_type, **hints):
    spec = {
        "type": arg_type, "types": [arg_type], "required": False,
        "description": "", "server_selectable": None, "choices": None,
        "initial": None,
    }
    spec.update(hints)
    return spec


class SliderWidgetTest(unittest.TestCase):
    """`ui: "slider"` on a bounded int/float renders the combined
    slider+spinbox; everything else stays a spin box."""

    def _one(self, spec):
        return formgen.build({"k": spec}, qt.QFormLayout())["k"]

    def test_bounded_float_with_the_hint_becomes_a_slider(self):
        widget = self._one(_numeric("float", ui="slider", min=-180, max=180, step=0.5, initial=10))

        self.assertIsInstance(widget, ctk.ctkSliderWidget)
        self.assertEqual(widget.minimum, -180.0)
        self.assertEqual(widget.maximum, 180.0)
        self.assertEqual(widget.singleStep, 0.5)
        self.assertEqual(widget.value, 10.0)

    def test_an_int_slider_reads_back_as_an_int(self):
        widget = self._one(_numeric("int", ui="slider", min=0, max=95, step=5, initial=5))

        self.assertEqual(widget.decimals, 0)
        value = formgen.collect({"k": widget})["k"]
        self.assertEqual(value, 5)
        self.assertIsInstance(value, int)

    def test_float_decimals_follow_the_step(self):
        widget = self._one(_numeric("float", ui="slider", min=0, max=1, step=0.05))
        self.assertEqual(widget.decimals, 2)

    def test_declared_decimals_win_over_the_step(self):
        widget = self._one(_numeric("float", ui="slider", min=0, max=1, step=0.05, decimals=4))
        self.assertEqual(widget.decimals, 4)

    def test_a_slider_without_bounds_falls_back_to_a_spin_box(self):
        # An unbounded slider has no geometry; the panel must render anyway.
        self.assertIsInstance(self._one(_numeric("float", ui="slider")), qt.QDoubleSpinBox)
        self.assertIsInstance(self._one(_numeric("int", ui="slider", min=0)), qt.QSpinBox)

    def test_bounds_without_the_hint_constrain_the_spin_box(self):
        # min/max alone must not switch the widget kind: a bound added
        # server-side for validation cannot silently produce a slider.
        widget = self._one(_numeric("int", min=1, max=10, step=2))

        self.assertIsInstance(widget, qt.QSpinBox)
        self.assertEqual((widget.minimum, widget.maximum), (1, 10))
        self.assertEqual(widget.singleStep, 2)

    def test_an_unknown_scalar_ui_falls_back_to_a_spin_box(self):
        self.assertIsInstance(self._one(_numeric("float", ui="dial")), qt.QDoubleSpinBox)

    def test_changing_the_slider_notifies(self):
        widget = self._one(_numeric("float", ui="slider", min=0, max=10))
        calls = []
        formgen.connect_changed(widget, lambda *_a: calls.append(1))

        widget.value = 3.5

        self.assertEqual(len(calls), 1)

    def test_slider_value_is_sent_in_clear(self):
        widget = self._one(_numeric("float", ui="slider", min=0, max=10, initial=2.5))
        data = ToolServerClient._stringify(formgen.collect({"k": widget}))
        self.assertEqual(data["k"], "2.5")

    def test_a_required_slider_at_zero_still_counts_as_filled(self):
        schema = {"k": _numeric("float", ui="slider", min=-10, max=10, required=True)}
        widgets = formgen.build(schema, qt.QFormLayout())
        self.assertTrue(formgen.all_required_filled(widgets, schema))


# ---------------------------------------------------------------------------
# vec2 (two numbers set together, ui = "joystick" for the 2D pad)
# ---------------------------------------------------------------------------

def _vec2(**hints):
    spec = {
        "type": "vec2", "types": ["vec2"], "required": False,
        "description": "", "server_selectable": None, "choices": None,
        "initial": None,
    }
    spec.update(hints)
    return spec


class JoystickWidgetTest(unittest.TestCase):
    def _one(self, spec):
        return formgen.build({"k": spec}, qt.QFormLayout())["k"]

    def test_vec2_with_the_hint_gets_a_pad_with_the_declared_ranges(self):
        widget = self._one(_vec2(ui="joystick", x_range=[-15, 15], y_range=[-5, 5]))

        self.assertIsInstance(widget, formgen.JoystickInput)
        self.assertIsNotNone(widget.pad)
        self.assertEqual((widget.pad.x_start, widget.pad.x_end), (-15.0, 15.0))
        self.assertEqual((widget.pad.y_start, widget.pad.y_end), (-5.0, 5.0))

    def test_vec2_without_the_hint_is_two_plain_spin_boxes(self):
        widget = self._one(_vec2(x_range=[0, 1], y_range=[0, 1]))
        self.assertIsInstance(widget, formgen.JoystickInput)
        self.assertIsNone(widget.pad)

    def test_an_unknown_vec2_ui_falls_back_to_the_boxes_alone(self):
        widget = self._one(_vec2(ui="trackball", x_range=[0, 1], y_range=[0, 1]))
        self.assertIsNone(widget.pad)

    def test_the_declared_initial_reaches_boxes_and_pad(self):
        widget = self._one(_vec2(ui="joystick", x_range=[-15, 15], y_range=[-5, 5], initial=[3, -2]))

        self.assertEqual(widget.value(), [3.0, -2.0])
        self.assertEqual((widget.pad.value_x, widget.pad.value_y), (3.0, -2.0))

    def test_no_initial_opens_at_the_centre_of_both_axes(self):
        widget = self._one(_vec2(ui="joystick", x_range=[0, 10], y_range=[-5, 5]))
        self.assertEqual(widget.value(), [5.0, 0.0])

    def test_collect_returns_the_pair_and_it_travels_as_json(self):
        widget = self._one(_vec2(ui="joystick", x_range=[-15, 15], y_range=[-5, 5], initial=[3, -2]))

        collected = formgen.collect({"k": widget})
        self.assertEqual(collected["k"], [3.0, -2.0])
        self.assertEqual(json.loads(ToolServerClient._stringify(collected)["k"]), [3.0, -2.0])

    def test_editing_a_box_moves_the_pad(self):
        widget = self._one(_vec2(ui="joystick", x_range=[-15, 15], y_range=[-5, 5], initial=[0, 0]))

        widget.xBox.setValue(7.5)

        self.assertEqual(widget.pad.value_x, 7.5)

    def test_moving_the_pad_updates_the_boxes(self):
        widget = self._one(_vec2(ui="joystick", x_range=[-15, 15], y_range=[-5, 5], initial=[0, 0]))

        widget.pad.setValues(4.0, -1.0, notify=True)

        self.assertEqual(widget.value(), [4.0, -1.0])

    def test_any_input_path_notifies(self):
        widget = self._one(_vec2(ui="joystick", x_range=[-15, 15], y_range=[-5, 5], initial=[0, 0]))
        calls = []
        formgen.connect_changed(widget, lambda *_a: calls.append(1))

        widget.yBox.setValue(2.0)       # typing
        widget.pad.setValues(1.0, 2.0, notify=True)  # dragging

        self.assertGreaterEqual(len(calls), 2)

    def test_a_spring_back_pad_accumulates_displacements(self):
        widget = self._one(_vec2(ui="joystick", x_range=[-10, 10], y_range=[-10, 10],
                                 initial=[0, 0], spring_back=True))
        pad = widget.pad

        # One push: offset (2, 1) from the rest position, then release.
        pad.setValues(2.0, 1.0, notify=True)
        self.assertEqual(widget.value(), [2.0, 1.0])
        pad.mouseReleaseEvent(None)
        self.assertEqual((pad.value_x, pad.value_y), (0.0, 0.0))  # sprang home

        # A second push adds to the committed base instead of replacing it.
        pad.setValues(1.0, 1.0, notify=True)
        self.assertEqual(widget.value(), [3.0, 2.0])

    def test_the_description_is_shown_above_the_widgets(self):
        widget = formgen._make_widget("k", _vec2(ui="joystick", description="Move the landmark"))
        laid_out = widget.container.layout.widgets
        self.assertIsInstance(laid_out[0], qt.QLabel)
        self.assertEqual(laid_out[0].text, "Move the landmark")

    def test_an_invalid_range_falls_back_to_the_unit_axis(self):
        widget = self._one(_vec2(ui="joystick", x_range=[3], y_range=[0, 1]))
        self.assertEqual((widget.pad.x_start, widget.pad.x_end), (0.0, 1.0))

    def test_a_required_vec2_always_counts_as_filled(self):
        schema = {"k": _vec2(ui="joystick", x_range=[0, 1], y_range=[0, 1], required=True)}
        widgets = formgen.build(schema, qt.QFormLayout())
        self.assertTrue(formgen.all_required_filled(widgets, schema))

class HostedEntryReadabilityTest(unittest.TestCase):
    """A hosted entry has to be readable in the popup, whatever the collapsed
    box's width. Elided, `CBCT_Or_FullyAuto_DCM (folder, 532 MB)` and
    `CBCT_Or_FullyAuto (folder, 267 MB)` are the same text -- and what gets cut
    is exactly what the entry exists to say."""

    def _input(self, hosted):
        widget = formgen.ServerFileInput(qt.QLineEdit())
        widget.setChoices(hosted)
        return widget

    def test_the_popup_is_widened_to_its_longest_entry(self):
        widget = self._input([
            {"name": "a.nii.gz", "kind": "file", "size": 1},
            {"name": "CBCT_Or_FullyAuto_DCM", "kind": "folder", "size": 532 * 1024 ** 2},
        ])

        longest = max(len(entry) for entry in widget._entries())
        self.assertGreaterEqual(widget.combo.view().minimumWidth, longest)

    def test_the_width_comes_from_the_view_not_from_font_metrics(self):
        """The trap this cost a release to find.

        `combo.fontMetrics` is a SLOT under PythonQt, not a property: reading
        through it without calling it raises `AttributeError`, which the bare
        `except` here swallowed -- so the list was never widened once, while the
        stub (which modelled it as a property) said it was. Measured in Slicer:
        `view.minimumWidth` 0, open popup 166 px, three AREG cohorts reading
        `CBCT_Or_Full...`.

        The view is asked instead, and it measures its own items.
        """
        widget = self._input([{"name": "CBCT_Or_FullyAuto_DCM", "kind": "folder",
                               "size": 532 * 1024 ** 2}])

        # Nothing may reach for the metrics through the slot object.
        with self.assertRaises(AttributeError):
            widget.combo.fontMetrics.horizontalAdvance("x")
        self.assertGreater(widget.combo.view().minimumWidth, 0)

    def test_a_view_that_cannot_measure_leaves_the_panel_standing(self):
        """Cosmetic, always: a list that is merely narrow must never be a
        traceback in a clinician's panel."""
        widget = self._input([{"name": "a.nii.gz", "kind": "file", "size": 1}])

        def refuse(_column):
            raise RuntimeError("no view today")

        widget.combo.view().sizeHintForColumn = refuse
        widget.setChoices([{"name": "b.nii.gz", "kind": "file", "size": 2}])

        self.assertEqual(widget.combo.itemText(1), "b.nii.gz  (file, 2 B)")

    def test_the_popup_is_bounded(self):
        """Wider than its box, never wider than a screen."""
        widget = self._input([{"name": "x" * 4000, "kind": "file", "size": 1}])

        self.assertLessEqual(widget.combo.view().minimumWidth,
                             formgen._POPUP_MAX_WIDTH)

    def test_the_collapsed_box_stays_narrow(self):
        """The row still has to fit: only the LIST grows."""
        widget = self._input([{"name": "x" * 120, "kind": "file", "size": 1}])

        self.assertEqual(widget.combo.minimumContentsLength, 14)

    def test_widening_survives_a_toolkit_that_cannot_do_it(self):
        """Every failure here is cosmetic, so none of them may take the panel
        down with it."""
        widget = self._input([{"name": "a.nii.gz", "kind": "file", "size": 1}])

        def explode():
            raise RuntimeError("no view in this build")

        widget.combo.view = explode
        widget.setChoices([{"name": "b.nii.gz", "kind": "file", "size": 2}])

        self.assertIn("b.nii.gz", " ".join(widget._entries()))

    def test_an_entry_says_what_it_is_and_what_it_costs(self):
        widget = self._input([
            {"name": "CBCT_FullyAuto", "kind": "folder", "size": 339 * 1024 ** 2},
        ])

        entry = [e for e in widget._entries() if "CBCT_FullyAuto" in e][0]
        self.assertIn("folder", entry)
        self.assertIn("339", entry)


if __name__ == "__main__":
    unittest.main()
