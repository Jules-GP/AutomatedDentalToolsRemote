"""Generic Slicer widget for any tool exposed by the tool server.

A concrete module declares TOOL_NAME, optionally overrides what its tool's
schema cannot state (FILE_INPUTS, RESULT_KIND) and optionally overrides a few
hooks; everything else — Slicer lifecycle, schema-driven GUI, theme, async
call, error handling, temp-file cleanup — is inherited from here.

See ARCHITECTURE.md, "How to add a new module in 5 minutes".
"""

import logging
import os
import re
import shutil
import time
import zipfile

import ctk
import qt
import slicer
from slicer.i18n import tr as _
from slicer.ScriptedLoadableModule import ScriptedLoadableModuleWidget
from slicer.util import VTKObservationMixin

from . import config, design, formgen, is_file_type, slicer_io, testfile_entries
from .errors import ServerToolError
from .worker import BackgroundJob

logger = logging.getLogger("ServerToolsCore.base_widget")

# "auto" is the schema-driven default: the argument's `types` decide whether it
# gets a file picker, a folder picker, or the choice between both (and which
# extensions the file picker offers). The explicit modes are for what the
# schema cannot express — picking a volume from the MRML scene — for forcing
# one selection kind, or ("none") for not offering an argument at all.
_FILE_INPUT_MODES = ("auto", "single_file", "folder_zip", "file_or_folder", "volume_node", "none")
_RESULT_KINDS = ("text", "segmentation", "labelmap", "volume", "model", "save_as")

# The box holding the output folder picker, which no schema argument owns. A
# tool may still put arguments of its own in it by declaring section="Outputs"
# (ASO's output_suffix does), which is why it is a plain name rather than a
# separate widget.
_OUTPUTS_SECTION = "Outputs"

# Characters a hosted test file's name may not contribute to a path built from
# it. The name comes from the server's own listing rather than from a user, but
# it is joined onto a local directory, and a "/" or a ".." in one would place
# the download somewhere nobody asked for.
_UNSAFE_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """A single, harmless path component for a hosted entry's name."""
    cleaned = _UNSAFE_NAME_CHARACTERS.sub("_", os.path.basename(name)).strip("._-")
    return cleaned or "test_file"


class _Run:
    """One tool execution: its own inputs, its own scratch directory, its own thread.

    A panel holds a LIST of these rather than a single job, because the wait a
    clinician actually has is a cohort, and the upload of the next patient has
    no reason to sit behind the inference of the previous one.

    What it does NOT buy is parallel inference: the server serialises the card
    (`MAX_CONCURRENT_GPU_JOBS`), so four runs of a segmentation still segment one
    at a time. The gain is the overlap of transfer with compute, which on a
    cohort is most of the wall clock, and genuine parallelism across tools that
    do not both want the GPU.
    """

    def __init__(self, number, label, args, files, output_dir, workspace):
        self.number = number
        self.label = label
        self.args = args
        self.files = files
        self.output_dir = output_dir
        self.workspace = workspace
        self.job = None
        self.phase = ""
        self.started_at = None  # None while the run is still queued

    @property
    def running(self) -> bool:
        return self.job is not None

    def cancel(self) -> None:
        if self.job:
            self.job.cancel()
            self.job = None
        self.close()

    def close(self) -> None:
        """Drop the scratch directory. Per run, never shared: two runs writing
        into one temp folder would overwrite each other's inputs."""
        if self.workspace:
            self.workspace.__exit__(None, None, None)
            self.workspace = None


class ServerToolWidgetBase(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Only TOOL_NAME is required. Everything the tool's own schema already
    states — which arguments are file inputs, what each picker looks like, what
    comes back — is derived from it (see formgen.file_input_modes and
    formgen.result_kind_for); the two attributes below are *overrides*, for the
    handful of things the server cannot know."""

    # -- declared by subclasses --------------------------------------
    TOOL_NAME = None
    # {schema_argument_name: mode} merged over the schema's own file arguments.
    # Only what the schema cannot say: "volume_node", a forced picker kind, or
    # "none" to leave an optional file argument out. See _FILE_INPUT_MODES.
    FILE_INPUTS = {}
    # None -> derived from the tool's output_kind. Declare one only when that
    # is ambiguous: output_kind "file" says a file comes back, not whether to
    # load it into the scene ("volume"/"model") or save it ("save_as").
    RESULT_KIND = None
    AUTO_UI = True

    # There is deliberately no TEST_DATA attribute any more. A tool's test data
    # is what the SERVER hosts for it (GET /tools/{tool}/data), offered in the
    # input row's own dropdown and downloaded on selection -- so every tool
    # gets it, and no module declares anything. What was here instead was a
    # per-module dict of hardcoded GitHub release URLs, which four modules had
    # and eleven did not, and which the server knew nothing about.

    def __init__(self, parent=None):
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)

        if not self.TOOL_NAME:
            raise ValueError(f"{type(self).__name__} must set TOOL_NAME.")
        for arg_name, mode in self.FILE_INPUTS.items():
            if mode not in _FILE_INPUT_MODES:
                raise ValueError(f"{type(self).__name__}: unknown file input mode '{mode}' for '{arg_name}'.")
        if self.RESULT_KIND is not None and self.RESULT_KIND not in _RESULT_KINDS:
            raise ValueError(f"{type(self).__name__}: unknown RESULT_KIND '{self.RESULT_KIND}'.")

        # Imported lazily to keep ServerToolsCoreLib importable outside Slicer for tests.
        from . import get_client

        self.client = get_client()
        self._argWidgets = {}
        self._schema = None
        # Active runs, queued and running, in the order they were asked for.
        # A list rather than one `_job`, so a second Apply enqueues instead of
        # being refused; `_pumpRuns` decides how many of them run at once.
        self._runs = []
        self._runsStarted = 0  # only ever grows: it numbers runs for the user
        self._inputWidgets = {}  # {schema_argument_name: widget}
        self._inputModes = {}  # {schema_argument_name: mode}, "auto" already resolved
        self._outputFolderWidget = None
        # Schema-driven panel layout, all rebuilt wholesale by _buildForm.
        self._sectionBoxes = {}  # {section name: ctkCollapsibleButton}
        self._sectionLayouts = {}  # {section name: QFormLayout}
        self._rows = {}  # {schema_argument_name: (label, field)} — hidden together
        self._rowSections = {}  # {schema_argument_name: section name}
        self._sectionsWithOwnRows = set()  # sections holding a row no argument owns
        self._hiddenArgs = set()  # arguments whose `visible_when` is not satisfied
        self._statusBadge = None
        self._statusJob = None
        self._downloadJob = None  # one test-file fetch at a time
        # Where a downloaded test file lands, and what is already there.
        # Created on first use and never in Documents: a 648 MB cohort a user
        # clicked once must not still be on their disk next month.
        self._testFileRoot = None
        self._testFileCache = {}  # {hosted name: local path already fetched}
        self._sceneVolumes = {}  # {display name: vtkMRMLScalarVolumeNode}
        self._schemaError = None  # set while the panel could not be built from a schema
        self._rootLayout = None
        self._formWidget = None  # the schema-driven part, replaced wholesale on a rebuild
        self.applyButton = None
        self.cancelButton = None
        self.uiWidget = None
        self._progressLabel = None
        self._elapsedTimer = None  # ticks once a second while any run is active

    # ------------------------------------------------------------------
    # Slicer lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        ScriptedLoadableModuleWidget.setup(self)

        self.uiWidget = qt.QWidget()
        self.layout.addWidget(self.uiWidget)
        rootLayout = qt.QVBoxLayout(self.uiWidget)

        self._statusBadge = design.status_badge()
        rootLayout.addWidget(self._statusBadge)

        # The schema-driven part lives in its own container so it can be thrown
        # away and rebuilt in place — see _buildForm.
        self._rootLayout = rootLayout
        self._buildForm()

        extraLayout = qt.QVBoxLayout()
        rootLayout.addLayout(extraLayout)
        self.addExtraWidgets(extraLayout)

        self.applyButton = design.primary_button(_("Apply"))
        self.cancelButton = design.danger_button(_("Cancel"))
        self.cancelButton.setVisible(False)
        rootLayout.addWidget(self.applyButton)
        rootLayout.addWidget(self.cancelButton)

        self._progressLabel = design.progress_label()
        rootLayout.addWidget(self._progressLabel)

        self.applyButton.clicked.connect(self.onApplyButton)
        self.cancelButton.clicked.connect(self.onCancelButton)

        # Without a trailing stretch, QVBoxLayout spreads its (Preferred-policy)
        # widgets across the whole module panel height instead of packing them
        # at the top — the same reason every hand-written .ui file in this repo
        # ends with a vertical spacer.
        rootLayout.addStretch(1)

        design.apply(self.uiWidget)

        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)
        # A volume loaded or removed while the module is open must appear in
        # (or leave) the input dropdowns without the user having to switch
        # modules and back; enter() alone cannot see it happen.
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.NodeAddedEvent, self._onSceneNodesChanged)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.NodeRemovedEvent, self._onSceneNodesChanged)

        self._checkCanApply()

        # Also kick off the health check here, not only in enter(): a module
        # reload re-instantiates the widget and calls setup() but never enter()
        # (see slicer.util.reloadScriptedModule), which would leave the freshly
        # created badge stuck on "checking..." until the user leaves the module
        # and comes back.
        self._refreshServerStatus()

    def cleanup(self) -> None:
        self.removeObservers()
        for run in list(self._runs):
            run.cancel()
        self._runs = []
        if self._statusJob:
            self._statusJob.cancel()
            self._statusJob = None
        if self._downloadJob:
            self._downloadJob.cancel()
            self._downloadJob = None
        self._removeOwnTestFiles()

    def enter(self) -> None:
        if self.uiWidget:
            design.apply(self.uiWidget)
        # The hosted-file lists are re-read here, not only at setup(). They are
        # server-side state that changes independently of the schema — a model
        # dropped into DATA/<tool>/models/ does not touch /tools — so nothing
        # in the schema-rebuild path (which only fires when the schema fetch
        # FAILED) can ever notice one. Without this, a bundle added while
        # Slicer is open is invisible until Slicer is restarted, with no
        # affordance on the panel saying so: the user sees a dropdown that is
        # simply missing the entry they were told to pick.
        # Swept on ENTER, not only when someone picks a test file. Slicer
        # never removes what `tempDirectory()` creates -- its own docstring
        # says so -- and a user who downloaded a 648 MB cohort once and did not
        # come back would keep it for good. Opening any tool is now enough.
        self._sweepLeftoverTestFiles()
        self._refreshServerSelectables()
        self._refreshSceneVolumes()
        self._refreshServerStatus()

    def exit(self) -> None:
        pass

    def onSceneStartClose(self, caller, event) -> None:
        pass

    def onSceneEndClose(self, caller, event) -> None:
        # The scene is empty now: the dropdowns must stop offering volumes
        # that no longer exist.
        if self.uiWidget:
            self._refreshSceneVolumes()

    # ------------------------------------------------------------------
    # GUI construction
    # ------------------------------------------------------------------

    def _buildForm(self, force_refresh: bool = False) -> None:
        """Build the schema-driven part of the panel into a fresh container,
        replacing the previous one if there was any.

        Called once from setup(), and again by _onStatusChecked when a server
        that was unreachable at setup() time comes back: the panel is built
        from the schema, so a failed fetch leaves nothing but an error label,
        and nothing else would ever clear it — the module would stay broken for
        the rest of the Slicer session even though the server is back.

        Replacing the whole container rather than clearing a layout keeps this
        simple and total: no widget of the previous attempt survives, including
        the error label and any stale server-side dropdown.
        """
        formWidget = qt.QWidget()
        formLayout = qt.QVBoxLayout(formWidget)
        formLayout.setContentsMargins(0, 0, 0, 0)

        try:
            if self.AUTO_UI:
                self._buildAutoUI(formLayout, force_refresh=force_refresh)
            else:
                self.buildCustomUI(formLayout)
        except Exception as exc:
            # Never leave the user with a silently blank/half-built panel: a bad
            # CTK/Qt call, a module misconfiguration, etc. must be visible right
            # here, not just in the Python console.
            logger.exception("Failed to build the UI for tool '%s'", self.TOOL_NAME)
            formLayout.addWidget(
                design.warning_label(_("Could not build this module's UI: {error}").format(error=exc))
            )

        previous = self._formWidget
        if previous is None:
            self._rootLayout.addWidget(formWidget)
        else:
            self._rootLayout.insertWidget(self._rootLayout.indexOf(previous), formWidget)
            # Hide and unparent so the old panel leaves the layout now, but let
            # Qt destroy it later: this can run from a signal emitted by one of
            # its own children (the Retry button below).
            previous.setVisible(False)
            previous.setParent(None)
            previous.deleteLater()
        self._formWidget = formWidget

        if previous is not None:
            # A rebuild: the stylesheet was applied to widgets that no longer
            # exist, and the Apply button's state was computed from them.
            design.apply(self.uiWidget)
            self._checkCanApply()

    def _onRetryButton(self) -> None:
        """Rebuild from a fresh /tools fetch. Safe to call from the button's own
        handler: _buildForm hides the old container and defers its destruction
        with deleteLater(), so the button outlives the click it is handling."""
        self._buildForm(force_refresh=True)
        self._refreshServerStatus()

    def _buildAutoUI(self, rootLayout, force_refresh: bool = False) -> None:
        logger.info("Building AUTO_UI for TOOL_NAME='%s' (FILE_INPUTS overrides=%s, RESULT_KIND=%s)",
                    self.TOOL_NAME, self.FILE_INPUTS, self.RESULT_KIND or "<from output_kind>")

        # The schema is fetched before any widget is built, not after: a file
        # argument's declared `types` decide what its picker looks like (file,
        # folder, or both — and with which extensions), so the widgets cannot
        # be built without it. The failure path below still builds them, from
        # an empty schema, so the panel is never blank.
        # _schemaError is what tells _onStatusChecked this panel is worth
        # rebuilding once the server answers again.
        self._schemaError = None
        try:
            self._schema = self.client.get_tool_schema(self.TOOL_NAME, force_refresh=force_refresh)
            logger.info(
                "Schema for '%s': output_kind=%s, argument keys=%s",
                self.TOOL_NAME,
                self._schema.get("output_kind"),
                sorted(self._schema.get("arguments", {}).keys()),
            )
        except ServerToolError as exc:
            logger.warning("Could not load schema for '%s': %s", self.TOOL_NAME, exc)
            self._schema = {"arguments": {}}
            self._schemaError = exc

        # One collapsible box per section the schema names, in declaration
        # order. A tool naming none gets exactly one box called "Inputs" — the
        # panel every module has today, unchanged.
        arguments = self._schema.get("arguments", {})
        # DEFAULT_SECTION is always created, even when every argument claims
        # another one: it is where anything without a section of its own goes,
        # including the error path's empty schema. An unused one holds no rows
        # and _applyVisibility hides it, so it costs nothing on screen.
        extraSections = [formgen.DEFAULT_SECTION]
        if self.resultKind == "save_as":
            extraSections.append(_OUTPUTS_SECTION)
        self._sectionBoxes = {}
        self._sectionLayouts = {}
        self._rows = {}
        self._rowSections = {}
        self._sectionsWithOwnRows = set()
        for sectionName in formgen.sections_of(arguments, extraSections):
            box = ctk.ctkCollapsibleButton()
            box.text = _(sectionName)
            self._sectionLayouts[sectionName] = qt.QFormLayout(box)
            self._sectionBoxes[sectionName] = box
            rootLayout.addWidget(box)

        inputsLayout = self._sectionLayouts[formgen.DEFAULT_SECTION]

        self._inputWidgets = self._buildInputWidgets(inputsLayout)

        if self._schemaError is not None:
            rootLayout.addWidget(
                design.warning_label(
                    _("Could not load '{tool}' from the server: {error}").format(
                        tool=self.TOOL_NAME, error=self._schemaError
                    )
                )
            )
            # Leaving and re-entering the module also retries (see
            # _onStatusChecked), but a user staring at this error should not
            # have to discover that.
            retryButton = design.primary_button(_("Retry"))
            retryButton.clicked.connect(self._onRetryButton)
            rootLayout.addWidget(retryButton)
        else:
            self._warnAboutFileInputsMismatch(rootLayout)

        self._argWidgets = formgen.build(
            arguments, inputsLayout, sections=self._sectionLayouts, rows=self._rows
        )
        self._rowSections.update(
            {name: formgen.section_of(arguments.get(name, {})) for name in self._rows}
        )
        logger.info("AUTO_UI built %d scalar field(s) for '%s': %s",
                    len(self._argWidgets), self.TOOL_NAME, sorted(self._argWidgets.keys()))
        for widget in self._argWidgets.values():
            formgen.connect_changed(widget, self._checkCanApply)

        self._populateServerSelectables(rootLayout)
        self._refreshSceneVolumes()

        if self.resultKind == "save_as":
            outputsLayout = self._sectionLayouts[_OUTPUTS_SECTION]
            self._outputFolderWidget = ctk.ctkPathLineEdit()
            self._outputFolderWidget.filters = ctk.ctkPathLineEdit.Dirs
            outputsLayout.addRow(design.required_label(_("Output folder")), self._outputFolderWidget)
            formgen.connect_changed(self._outputFolderWidget, self._checkCanApply)
            # This row belongs to no schema argument, so it must keep its
            # section on screen even when every argument in it is hidden.
            self._sectionsWithOwnRows.add(_OUTPUTS_SECTION)

        self._wireVisibility(arguments)

        self.configureFields()

    # ------------------------------------------------------------------
    # Conditional fields (`visible_when`)
    # ------------------------------------------------------------------

    def _wireVisibility(self, arguments: dict) -> None:
        """Re-evaluate every `visible_when` whenever a controlling field
        changes, and once now so the panel opens in the right state.

        Called from _buildAutoUI rather than from setup(), because the panel is
        rebuilt from scratch when a server that was down comes back — the same
        reason configureFields() exists (see ARCHITECTURE.md). Wiring this once
        at setup() would leave the rebuilt panel showing every field of every
        mode again.
        """
        for name in formgen.controlling_arguments(arguments):
            widget = self._argWidgets.get(name)
            if widget is None:
                # check_schema rejects a visible_when naming an argument the
                # tool doesn't declare, so this means the schema fetch failed
                # and there is no form to drive. is_visible() hides what it
                # cannot evaluate, which is already the right answer.
                continue
            formgen.connect_changed(widget, self._applyVisibility)
        self._applyVisibility()

    def _narrowChoices(self, name: str, allowed) -> None:
        """Restrict one combo box to `allowed`, keeping the selection if it
        survives. Falls back to the first option, because a QComboBox cannot be
        empty and index 0 is what it would select anyway."""
        widget = self._argWidgets.get(name)
        if widget is None or not hasattr(widget, "addItems"):
            return
        current = widget.currentText
        if [widget.itemText(i) for i in range(widget.count)] == list(allowed):
            return
        was = widget.blockSignals(True)
        widget.clear()
        widget.addItems(list(allowed))
        widget.blockSignals(was)
        index = widget.findText(current)
        widget.setCurrentIndex(index if index >= 0 else 0)

    def _applyVisibility(self, *_args) -> None:
        arguments = (self._schema or {}).get("arguments", {})
        controlling = formgen.controlling_arguments(arguments)
        values = formgen.collect(
            {name: self._argWidgets[name] for name in controlling if name in self._argWidgets}
        )

        # Narrow the choice boxes BEFORE deciding what is visible: an option
        # the current mode does not have must not merely fail at the end of a
        # run, and re-selecting here can itself change what the rest of the
        # panel shows.
        for name, spec in arguments.items():
            allowed = formgen.allowed_options(spec, values)
            if allowed is not None:
                self._narrowChoices(name, allowed)
        values = formgen.collect(
            {name: self._argWidgets[name] for name in controlling if name in self._argWidgets}
        )

        hidden = set()
        for name, spec in arguments.items():
            visible = formgen.is_visible(spec, values)
            if not visible:
                hidden.add(name)
            for widget in self._rows.get(name, ()):
                widget.setVisible(visible)
        self._hiddenArgs = hidden

        # A section whose every row is hidden is an empty titled box; hide it
        # too. This is what turns two mutually exclusive sets of arguments into
        # the old module's two stacked pages, with no client-side notion of a
        # "page" anywhere.
        for sectionName, box in self._sectionBoxes.items():
            owned = [name for name, owner in self._rowSections.items() if owner == sectionName]
            box.setVisible(
                sectionName in self._sectionsWithOwnRows
                or any(name not in hidden for name in owned)
            )

        self._checkCanApply()

    def configureFields(self) -> None:
        """Override to touch up the generated widgets once they all exist —
        a placeholder, an initial value, a connection between two fields.

        Called at the end of every auto-generated panel build, `addExtraWidgets`
        only at the first: the panel is rebuilt from scratch when a server that
        was down at setup() time comes back (see _buildForm), and anything
        applied outside this hook would be lost on that rebuild, leaving a
        subtly different panel from the one the module describes.

        `self._argWidgets` and `self._inputWidgets` are populated by now; both
        are empty when the schema could not be fetched, so read them with
        `.get()`.
        """

    @property
    def resultKind(self) -> str:
        """RESULT_KIND if the module declares one, otherwise derived from the
        tool's own output_kind (see formgen.result_kind_for)."""
        return formgen.result_kind_for((self._schema or {}).get("output_kind"), self.RESULT_KIND)

    def _buildInputWidgets(self, layout) -> dict:
        # Resolved once, here: each mode is needed both to build the widget and,
        # later, to know whether the selection has to be zipped before upload.
        self._inputModes = formgen.file_input_modes(
            (self._schema or {}).get("arguments", {}), self.FILE_INPUTS
        )
        return {
            arg_name: self._buildFileInputWidget(layout, arg_name, mode)
            for arg_name, mode in self._inputModes.items()
        }

    def _schemaArgument(self, arg_name: str) -> dict:
        return (self._schema or {}).get("arguments", {}).get(arg_name, {})

    def _buildFileInputWidget(self, layout, arg_name: str, mode: str):
        spec = self._schemaArgument(arg_name)
        # Same rule as every other row (formgen.label_for): the tool's own
        # wording when it declares one, the prettified name otherwise. Not
        # wrapped in _(): a label coming from the server is not in this
        # module's translation catalog, and the fallback is a schema
        # identifier rather than a phrase anyone wrote.
        label = formgen.label_for(arg_name, spec)
        # A file argument goes in the section its own spec names, like every
        # other argument; `layout` is the fallback for one that names none.
        section = formgen.section_of(spec)
        target = self._sectionLayouts.get(section, layout)
        labelWidget = (
            design.required_label(label)
            if spec.get("required")
            else design.optional_label(label)
        )

        if mode == "volume_node":
            widget = slicer.qMRMLNodeComboBox()
            widget.nodeTypes = ["vtkMRMLScalarVolumeNode"]
            widget.noneEnabled = True
            widget.setMRMLScene(slicer.mrmlScene)
            target.addRow(labelWidget, widget)
            field = widget
            widget.currentNodeChanged.connect(self._checkCanApply)
        else:
            widget = formgen.file_widget(spec, mode)
            field = formgen.row_widget(widget)
            target.addRow(labelWidget, field)
            formgen.connect_changed(widget, self._checkCanApply)
            # Picking one of the tool's hosted test files is an action, not a
            # value: this is where it lands, and the download that follows is
            # why formgen hands the choice back instead of acting on it.
            setter = getattr(widget, "setHostedCallback", None)
            if setter is not None:
                setter(lambda name, arg=arg_name: self._onHostedTestFile(arg, name))

        # Recorded like a scalar row so `visible_when` can hide a file input
        # too, and so a section holding only file inputs is not mistaken for an
        # empty one.
        self._rows[arg_name] = (labelWidget, field)
        self._rowSections[arg_name] = section

        # The server's own wording for this input, now that the schema is known.
        description = spec.get("description")
        if description:
            widget.setToolTip(description)
        return widget

    def _serverSelectableArguments(self) -> dict:
        """`{argument name: "model" | "testfile"}` for every dropdown fed by
        GET /tools/{tool}/data.

        Two widget kinds, one mechanism: a SCALAR server_selectable argument
        (a model, which must never leave the server) is a plain combo box in
        `_argWidgets`; a FILE-typed one is an input row that also offers the
        hosted names, in `_inputWidgets`. Both are filled from the same call.
        """
        arguments = (self._schema or {}).get("arguments", {})
        return {
            name: spec["server_selectable"]
            for name, spec in arguments.items()
            if spec.get("server_selectable")
            and (name in self._argWidgets or name in self._inputWidgets)
        }

    def _fillServerSelectable(self, arg_name: str, kind: str, data: dict) -> list:
        """Put the hosted names into one dropdown and return them.

        **The current selection survives if the server still offers it.** This
        is refilled on every `enter()`, so without it, switching away from the
        module and back would silently reset a chosen model to the first entry
        in the list — the kind of change a user does not look for, and which
        would then run the tool against weights they never picked.
        """
        choices = list(data.get("models" if kind == "model" else "testfiles", []))
        fileInput = self._inputWidgets.get(arg_name)

        if fileInput is not None:
            # A file input needs no "(automatic)" entry: it leads with its own
            # prompt, so it can express "nothing chosen here". The current
            # selection surviving the refill lives inside the widget: its
            # rebuild keeps the entry when the server still offers it.
            #
            # It is fed the ENTRIES, not the bare names: what a user needs
            # before clicking a test file is whether it is one scan or a whole
            # cohort and how many bytes that is. A model dropdown gets the
            # names, because naming a model is all that ever travels for one.
            fileInput.setChoices(
                testfile_entries(data) if kind == "testfile" else choices
            )
            return choices

        spec = (self._schema or {}).get("arguments", {}).get(arg_name, {})
        entries = list(choices)
        if not spec.get("required"):
            entries.insert(0, formgen.AUTOMATIC_OPTION)

        widget = self._argWidgets[arg_name]
        previous = widget.currentText
        widget.clear()
        widget.addItems(entries)
        if previous in entries:
            widget.setCurrentIndex(entries.index(previous))
        return choices

    def _refreshServerSelectables(self) -> None:
        """Re-read the hosted-file lists and update the dropdowns in place.

        Called from `enter()`. Deliberately quieter than the build-time pass:
        it adds no warning label (there is no root layout to attach one to
        outside a build, and a banner appended on every visit to the module
        would accumulate), and a failure leaves the dropdowns exactly as they
        were — the panel is already usable, so a server that has gone away
        between two visits must not empty a working list.
        """
        selectable = self._serverSelectableArguments()
        if not selectable:
            return
        try:
            data = self.client.list_tool_data(self.TOOL_NAME)
        except ServerToolError as exc:
            logger.warning(
                "Could not refresh server-side data for '%s': %s", self.TOOL_NAME, exc
            )
            return

        for arg_name, kind in selectable.items():
            self._fillServerSelectable(arg_name, kind, data)
        # The refill can add the very entry that makes a required argument
        # satisfiable, or remove the one that was satisfying it.
        self._checkCanApply()

    def _populateServerSelectables(self, rootLayout) -> None:
        """Fill every server_selectable dropdown (see formgen._make_widget)
        with the file names hosted on the server for this tool, from
        GET /tools/{tool}/data — e.g. SurgMovPred's "model" argument, which
        is picked among the server's models by name, never uploaded.

        Synchronous like the schema fetch just above, and for the same reason:
        the form needs its choices before the first paint, and the call is
        capped at the same short timeout. A failure (or an empty list) shows a
        visible warning instead of leaving a silently empty dropdown.
        """
        selectable = self._serverSelectableArguments()
        if not selectable:
            return

        try:
            data = self.client.list_tool_data(self.TOOL_NAME)
        except ServerToolError as exc:
            logger.warning("Could not list server-side data for '%s': %s", self.TOOL_NAME, exc)
            rootLayout.addWidget(
                design.warning_label(
                    _("Could not list the server-side files for '{tool}': {error}").format(
                        tool=self.TOOL_NAME, error=exc
                    )
                )
            )
            return

        for arg_name, kind in selectable.items():
            choices = self._fillServerSelectable(arg_name, kind, data)
            fileInput = self._inputWidgets.get(arg_name)
            logger.info("Populated '%s.%s' with %d server-side %s(s)",
                        self.TOOL_NAME, arg_name, len(choices), kind)
            # An empty list only blocks the user when there is no other way to
            # provide the argument. A file-typed one can always be uploaded
            # instead, so warning about it would be noise on every server that
            # simply hosts no test data.
            if not choices and fileInput is None:
                rootLayout.addWidget(
                    design.warning_label(
                        _("No {kind} available on the server for '{tool}' — ask the server maintainer to add one.").format(
                            kind=kind, tool=self.TOOL_NAME
                        )
                    )
                )

    def _warnAboutFileInputsMismatch(self, rootLayout) -> None:
        """Catch schema drift early. The set of file inputs is derived from the
        schema and so cannot drift; FILE_INPUTS *overrides* are written by hand
        against a remembered schema, so an override naming an argument the
        server no longer declares as a file surfaces immediately here instead
        of being silently ignored (or failing later with a confusing 422)."""
        declared = {name for name, spec in self._schema.get("arguments", {}).items() if is_file_type(spec.get("type", ""))}
        missing = set(self.FILE_INPUTS) - declared
        if missing:
            message = _(
                "FILE_INPUTS declares {missing} but the server's '{tool}' schema doesn't have "
                "them as file arguments (it has: {declared})."
            ).format(missing=sorted(missing), tool=self.TOOL_NAME, declared=sorted(declared))
            logger.warning(message)
            rootLayout.addWidget(design.warning_label(message))

    def buildCustomUI(self, layout) -> None:
        """Override when AUTO_UI = False."""
        raise NotImplementedError(f"{type(self).__name__} must implement buildCustomUI() since AUTO_UI is False.")

    def addExtraWidgets(self, layout) -> None:
        """Override to add a custom button or field. Called after the auto-generated
        GUI, before Apply/Cancel — this is the supported way to extend a module
        without touching setup()."""

    # ------------------------------------------------------------------
    # Overridable data hooks
    # ------------------------------------------------------------------

    def collectArgs(self) -> dict:
        """Override to transform values before sending.

        An OPTIONAL text field left empty is dropped rather than sent as "".
        The server applies an omitted optional argument's default; it takes a
        present one literally, so sending "" is asking for an empty value, not
        for the default. That is never what an untouched field means — for
        ALI's `prediction_ID` it produced `scan_lm_.mrk.json` instead of
        `scan_lm_Pred.mrk.json`.

        Only `""` qualifies: a multichoice reads back as a dict (every box
        unchecked is a meaningful selection, see MultiChoiceGroup), and 0 /
        False are values a user deliberately set.

        An argument HIDDEN by its `visible_when` is dropped for the same
        reason, one step further: it is not "left empty", it does not apply at
        all. Sending ASO's 32 `ios_teeth` boxes along with a CBCT run would
        state a selection the user was never shown and never made — and, since
        the server reads what it receives as the selection itself, an argument
        whose default someone changes server-side would still arrive frozen at
        whatever the invisible widget happened to hold.
        """
        values = formgen.collect(self._argWidgets)
        arguments = (self._schema or {}).get("arguments", {})
        collected = {
            name: value
            for name, value in values.items()
            if name not in self._hiddenArgs
            and not (value == "" and not arguments.get(name, {}).get("required"))
        }

        # A file argument satisfied by a hosted MODEL travels as a plain form
        # value - its NAME - not as an upload, so it belongs here rather than
        # in prepareInputFiles. The weights never move, which is the point.
        #
        # A hosted TEST FILE no longer appears here at all. It used to: the
        # name travelled and the server read the file in place. It is
        # downloaded now, so by the time a run starts it is an ordinary local
        # file in `files` - which is what lets the user open the scan beside
        # the panel, the whole reason for fetching it.
        collected.update(self._serverSideSelections())
        return collected

    def _serverSideSelections(self) -> dict:
        """{argument name: hosted name} for every input row whose selection is
        sent as a name rather than uploaded - a model, never a test file (see
        formgen.ServerFileInput.server_name)."""
        chosen = {}
        for arg_name, widget in self._inputWidgets.items():
            if arg_name in self._hiddenArgs:
                continue
            reader = getattr(widget, "server_name", None)
            name = reader() if reader else ""
            if name:
                chosen[arg_name] = name
        return chosen

    def prepareInputFiles(self, workspace: slicer_io.TempWorkspace) -> dict:
        """Override for exotic input cases. Default behavior covers every file
        input mode, for each of the tool's file arguments. Returns
        {schema_argument_name: local_file_path}."""
        files = {}
        for arg_name, mode in self._inputModes.items():
            path = self._prepareOneInputFile(workspace, arg_name, mode)
            if path is not None:
                files[arg_name] = path
        return files

    def _prepareOneInputFile(self, workspace: slicer_io.TempWorkspace, arg_name: str, mode: str):
        # Hidden by its `visible_when`: the argument does not apply to this
        # run, so nothing is uploaded for it — same rule as collectArgs.
        if arg_name in self._hiddenArgs:
            return None
        widget = self._inputWidgets.get(arg_name)
        # Satisfied by a volume already open in the scene: export it and send
        # it like any local file. The node is resolved through the same map
        # the dropdown was filled from (_refreshSceneVolumes).
        volume = getattr(widget, "volume_name", None)
        if volume and volume():
            node = self._sceneVolumes.get(volume())
            if node is None:
                return None
            return slicer_io.export_volume(
                node, workspace.file(f"{self.TOOL_NAME}_{arg_name}.nii.gz")
            )
        # Already satisfied by a MODEL the server hosts: nothing to upload,
        # collectArgs sends its name instead (see _serverSideSelections). A
        # hosted test file never reaches this line - it was downloaded, and the
        # row holds its local path.
        reader = getattr(widget, "server_name", None)
        if reader and reader():
            return None
        # Nothing chosen. That is a legitimate state for an OPTIONAL file
        # argument -- Apply no longer waits for one (see _inputReady) -- and the
        # answer is to upload nothing, so the server applies whatever it does
        # when the argument is absent. Returning `widget.currentPath` here sent
        # the empty string on as a path, and the very next thing to touch it
        # failed with "No such file or directory: ''", naming nothing the user
        # could act on. A required argument cannot reach this line: Apply is
        # disabled until it has a path.
        if mode in ("single_file", "folder_zip", "file_or_folder") and not widget.currentPath:
            return None
        if mode == "single_file":
            return widget.currentPath
        if mode == "folder_zip":
            return self._zipFolder(workspace, arg_name, widget.currentPath)
        if mode == "file_or_folder":
            # HTTP carries no folder: a folder selection goes up as a .zip,
            # which the server extracts (stripping a lone root directory).
            # Which one the user gave is read off the path itself — they never
            # had to declare it, so they cannot have declared it wrong.
            if widget.is_folder():
                return self._zipFolder(workspace, arg_name, widget.currentPath)
            return widget.currentPath
        if mode == "volume_node":
            node = widget.currentNode()
            if node is None:
                return None
            return slicer_io.export_volume(node, workspace.file(f"{self.TOOL_NAME}_{arg_name}.nii.gz"))
        return None

    def _zipFolder(self, workspace: slicer_io.TempWorkspace, arg_name: str, folder: str) -> str:
        return slicer_io.zip_folder(folder, workspace.file(f"{self.TOOL_NAME}_{arg_name}.zip"))

    def handleResult(self, result) -> None:
        """Override for custom result display."""
        kind = self.resultKind
        if kind == "text":
            slicer.util.infoDisplay(result.text or "")
        elif kind in ("segmentation", "labelmap", "volume", "model"):
            slicer_io.load_result(result.path, kind)
        elif kind == "save_as":
            self._handleSaveAsResult(result)

    def _handleSaveAsResult(self, result) -> None:
        """A "save_as" tool may return either one file as-is (e.g. SurgMovPred's
        single predictions_outputs.xlsx) or several files bundled into a .zip
        by the server-side wrapper (since one HTTP response can only carry one
        blob). Only unpack a genuine `.zip` — do NOT sniff the file's bytes for
        a zip signature: .xlsx/.docx/.ods are themselves zip containers
        (OOXML), so that would "extract" a result spreadsheet into raw XML
        parts instead of keeping it as the file it is."""
        if slicer_io.is_extractable_archive(result.path):
            resultDir = os.path.dirname(result.path)
            # Unpacking runs on the main thread and a result archive can expand
            # far beyond its own size (label volumes compress ~100x), so say so
            # before starting rather than letting the panel look frozen again.
            # processEvents is what actually paints it: without it the label is
            # only repainted once the (blocking) extraction is already done.
            self._showPhase(_("Extracting results..."))
            slicer.app.processEvents()
            try:
                slicer_io.unzip_folder(result.path, resultDir)
            finally:
                self._hideProgress()
            os.remove(result.path)
            slicer.util.infoDisplay(_("Results saved to {path}").format(path=resultDir))
        else:
            slicer.util.infoDisplay(_("Result saved to {path}").format(path=result.path))

    # ------------------------------------------------------------------
    # Apply / cancel
    # ------------------------------------------------------------------

    def _checkCanApply(self, *_args) -> None:
        if not self.applyButton:
            return  # a widget signal fired while the panel is still being built
        arguments = (self._schema or {}).get("arguments", {})
        canApply = self._inputReady() and formgen.all_required_filled(
            self._argWidgets, arguments, hidden=self._hiddenArgs
        )
        if self.resultKind == "save_as":
            canApply = canApply and bool(self._outputFolderWidget and self._outputFolderWidget.currentPath)
        self.applyButton.enabled = canApply

    def _inputReady(self) -> bool:
        arguments = (self._schema or {}).get("arguments", {})
        for arg_name, mode in self._inputModes.items():
            # A file input hidden by its `visible_when` is not uploaded either
            # (see _prepareOneInputFile), so it cannot be what Apply waits for.
            if arg_name in self._hiddenArgs:
                continue
            # Neither can an OPTIONAL one. `all_required_filled` has always
            # skipped `required: false` scalars; this loop did not, so any
            # optional file argument disabled Apply until something was picked
            # for it -- with no way to tell from the panel that the field was
            # what Apply was waiting for. AREG is the first tool to have one:
            # its `mgl_landmarks` exists only to REUSE landmarks you already
            # have, since the server predicts them otherwise, and requiring it
            # made the ordinary run the one you could not launch.
            if not arguments.get(arg_name, {}).get("required", True):
                continue
            widget = self._inputWidgets.get(arg_name)
            if mode == "volume_node":
                if widget is None or widget.currentNode() is None:
                    return False
                continue
            if widget is None:
                return False
            # A hosted MODEL satisfies the argument just as well as a local
            # file, and leaves currentPath empty on purpose (see
            # ServerFileInput). A hosted TEST FILE needs no clause of its own:
            # it becomes a local path the moment its download lands, and until
            # then Apply stays disabled, which is the honest state.
            reader = getattr(widget, "server_name", None)
            if reader and reader():
                continue
            # So does a volume already open in the scene: it is exported at
            # upload time (_prepareOneInputFile).
            volume = getattr(widget, "volume_name", None)
            if volume and volume():
                continue
            if not widget.currentPath:
                return False
        return True

    def onApplyButton(self) -> None:
        """Queue one run. Apply stays available, so clicking it again adds another.

        The inputs are read HERE, not when the run starts: what the panel says
        now is what the user asked for. A run that starts three minutes later
        because two others were ahead of it must not silently pick up whatever
        the pickers hold by then.
        """
        workspace = slicer_io.TempWorkspace()
        workspace.__enter__()

        try:
            files = self.prepareInputFiles(workspace)
            args = self.collectArgs()
        except Exception as exc:
            workspace.__exit__(None, None, None)
            slicer.util.errorDisplay(str(exc))
            return

        outputDir = self._outputFolderWidget.currentPath if self._outputFolderWidget else workspace.path

        self._runsStarted += 1
        self._runs.append(_Run(self._runsStarted, self._runLabel(files),
                               args, files, outputDir, workspace))
        self._pumpRuns()

    def _runLabel(self, files: dict) -> str:
        """Name a run after what it was given, so several lines of progress read.

        The tool name alone would make every line of a cohort identical, which
        is exactly when a user needs to tell them apart.
        """
        for path in files.values():
            if isinstance(path, str) and path:
                name = os.path.basename(path.rstrip(os.sep))
                if name:
                    return name
        return self.TOOL_NAME

    def _concurrentRuns(self) -> int:
        """How many runs may be in flight at once, never below one."""
        try:
            return max(1, int(config.CONCURRENT_RUNS))
        except (AttributeError, TypeError, ValueError):
            return 1

    def _pumpRuns(self) -> None:
        """Start queued runs up to the admission limit.

        This one mechanism serves both shapes a clinician might want, and the
        only thing between them is the number: 1 is a queue that works a cohort
        one patient at a time, N lets N transfers overlap one inference.
        """
        limit = self._concurrentRuns()
        for run in self._runs:
            if run.running:
                continue
            if sum(1 for other in self._runs if other.running) >= limit:
                break
            self._startRun(run)
        self._syncRunControls()

    def _startRun(self, run) -> None:
        def task(progress_cb):
            return self.client.run(
                self.TOOL_NAME,
                args=run.args,
                files=run.files,
                output_dir=run.output_dir,
                progress_cb=progress_cb,
            )

        run.phase = _("Sending request...")
        run.started_at = time.monotonic()
        # `run=run` binds the loop variable at definition time: without it every
        # callback would report against whichever run was queued last.
        run.job = BackgroundJob(
            task,
            on_success=lambda result, run=run: self._onJobSuccess(run, result),
            on_error=lambda exc, run=run: self._onJobError(run, exc),
            on_progress=lambda message, run=run: self._onJobProgress(run, message),
        )
        run.job.start()
        self._startElapsedTimer()

    def onCancelButton(self) -> None:
        """Cancel everything in flight, queued runs included.

        One button for the lot rather than one per line: a user who wants out
        wants out of all of it, and the in-flight HTTP request cannot be
        interrupted anyway (see ARCHITECTURE.md limitations).
        """
        for run in list(self._runs):
            run.cancel()
        self._runs = []
        self._stopElapsedTimer()
        self._syncRunControls()
        self._checkCanApply()
        slicer.util.showStatusMessage(_("Cancelled."), 3000)

    def _onJobSuccess(self, run, result) -> None:
        self._finishRun(run)
        with slicer.util.tryWithErrorDisplay(_("Failed to handle the tool result."), waitCursor=False):
            self.handleResult(result)

    def _onJobError(self, run, exc) -> None:
        """One run failing takes only that run: the rest of a cohort goes on."""
        self._finishRun(run)
        slicer.util.errorDisplay(str(exc))

    def _onJobProgress(self, run, message) -> None:
        # Kept as the phase, not printed once and forgotten: the elapsed-time
        # tick below re-renders it every second, so the panel keeps saying what
        # it is doing rather than showing a message frozen minutes ago.
        run.phase = message
        self._renderProgress()

    def _finishRun(self, run) -> None:
        run.job = None
        run.close()
        if run in self._runs:
            self._runs.remove(run)
        if not self._runs:
            self._stopElapsedTimer()
        # Admission frees up as this one leaves, so the next queued run starts
        # here -- that is what makes a cohort walk itself.
        self._pumpRuns()
        self._checkCanApply()

    def _syncRunControls(self) -> None:
        """Apply stays visible whatever is running: clicking it queues another."""
        if self.cancelButton is not None:
            self.cancelButton.setVisible(bool(self._runs))
            self.cancelButton.setText(_("Cancel all") if len(self._runs) > 1 else _("Cancel"))
        if self.applyButton is not None:
            self.applyButton.setVisible(True)
        self._renderProgress()

    # ------------------------------------------------------------------
    # "Still working" feedback
    # ------------------------------------------------------------------

    def _startElapsedTimer(self) -> None:
        """Tick once a second for as long as any run is active.

        The worker thread cannot report progress while it is blocked inside a
        single HTTP request, and that request IS the run -- minutes of remote
        inference with no bytes flowing either way. Only a main-thread timer
        can show the panel is alive during it, and without one the run looks
        hung: an AMASSS run was cancelled at three minutes because of this,
        having done nothing wrong and with 40 seconds left to go.

        Idempotent: one timer renders every run, and starting a second run must
        not leave the first one's timer running unowned.
        """
        if self._elapsedTimer is None:
            self._elapsedTimer = qt.QTimer()
            self._elapsedTimer.setInterval(1000)
            self._elapsedTimer.timeout.connect(self._renderProgress)
            self._elapsedTimer.start()
        self._renderProgress()

    def _stopElapsedTimer(self) -> None:
        if self._elapsedTimer:
            self._elapsedTimer.stop()
            self._elapsedTimer = None
        self._hideProgress()

    def _showPhase(self, message: str) -> None:
        """Put a message on the panel immediately, timer running or not.

        Deliberately independent of the elapsed-time state: _onJobSuccess tears
        the job down BEFORE handleResult, so the work that happens after it
        (unpacking an archive, loading nodes) has no timer left to render with
        and would otherwise report nothing at all.
        """
        if self._progressLabel is None:
            return
        self._progressLabel.setText(message)
        self._progressLabel.setVisible(True)
        slicer.util.showStatusMessage(message)

    def _hideProgress(self) -> None:
        if self._progressLabel:
            self._progressLabel.setVisible(False)
            self._progressLabel.setText("")

    def _renderProgress(self) -> None:
        if not self._runs:
            return
        self._showPhase("\n".join(self._describeRun(run) for run in self._runs))

    def _describeRun(self, run) -> str:
        """One line per run -- and for a single run, exactly the line it always was.

        The run number and label appear only once there is something to tell
        apart. A panel running one job must look like it always did: that is the
        overwhelmingly common case, and nine modules' worth of habit.
        """
        if run.started_at is None:
            return _("Run {number} ({label})  —  queued").format(
                number=run.number, label=run.label)
        elapsed = int(time.monotonic() - run.started_at)
        line = _("{phase}  —  {minutes}:{seconds:02d} elapsed").format(
            phase=run.phase or _("Working..."),
            minutes=elapsed // 60, seconds=elapsed % 60,
        )
        if len(self._runs) == 1:
            return line
        return _("Run {number} ({label}): {line}").format(
            number=run.number, label=run.label, line=line)

    # ------------------------------------------------------------------
    # Open volumes offered as input sources
    # ------------------------------------------------------------------

    def _onSceneNodesChanged(self, caller=None, event=None) -> None:
        self._refreshSceneVolumes()

    def _refreshSceneVolumes(self) -> None:
        """Re-offer the scene's scalar volumes in every input dropdown that
        can take one (formgen.accepts_volume).

        The display names are disambiguated here and mapped back to nodes at
        upload time through _sceneVolumes: two loaded volumes can share a
        name, and the dropdown must not let one silently shadow the other.
        """
        volumes = {}
        try:
            nodes = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
        except Exception:
            nodes = []
        for node in nodes:
            name = node.GetName()
            unique, counter = name, 2
            while unique in volumes:
                unique = f"{name} ({counter})"
                counter += 1
            volumes[unique] = node
        self._sceneVolumes = volumes

        for arg_name, widget in self._inputWidgets.items():
            setter = getattr(widget, "setVolumeChoices", None)
            if setter is not None and formgen.accepts_volume(self._schemaArgument(arg_name)):
                setter(list(volumes))
        self._checkCanApply()

    # ------------------------------------------------------------------
    # The tool's own test files, downloaded from the server on selection
    # ------------------------------------------------------------------

    # Our own key, so the sweep below can tell OUR leftovers from every other
    # module's use of slicer.util.tempDirectory().
    TEST_FILE_DIR_KEY = "ADTRemoteTestFiles"

    # Written inside each directory so the sweep can ask "is the session that
    # made this still running?" instead of guessing from a timestamp. An age
    # threshold was the first attempt and it was wrong in both directions: at
    # twelve hours, thirty-five directories and 2.4 GB piled up in a single
    # afternoon of launching Slicer, while a session left open longer than the
    # threshold could have had its own cohort deleted underneath it.
    OWNER_FILE = ".owner-pid"

    def _testFileDir(self) -> str:
        """A directory for this session's downloaded test files, created once.

        `slicer.util.tempDirectory()` and not ~/Documents: these are whole
        cohorts (648 MB for the semi-automated CBCT set) and a user who clicked
        a test file once to look at it has not asked to keep it. It also hands
        back a NEW directory per call, which is why the first one is kept -- a
        second pick of the same entry has to find the first one's bytes.

        Slicer's own docstring says it plainly: "This directory is not
        automatically cleaned up." The name carries a timestamp, so every
        session makes another one and nothing ever removes them -- 648 MB per
        cohort, per launch, until the operating system gets round to /tmp,
        which on a workstation left running is never. So this sweeps what
        earlier sessions left before making today's.
        """
        if not self._testFileRoot:
            self._sweepLeftoverTestFiles()
            self._testFileRoot = slicer.util.tempDirectory(key=self.TEST_FILE_DIR_KEY)
            try:
                with open(os.path.join(self._testFileRoot, self.OWNER_FILE), "w") as handle:
                    handle.write(str(os.getpid()))
            except OSError:  # the sweep will fall back to leaving it alone
                logger.debug("could not mark the test-file directory", exc_info=True)
        return self._testFileRoot

    @classmethod
    def _sweepLeftoverTestFiles(cls) -> None:
        """Remove test-file directories earlier sessions left behind.

        Ours only, by key, and only ones whose owning process is gone -- so a
        second Slicer running right now keeps its own, however long it has been
        open. A directory with no owner mark is from a build before this and is
        removed. Every failure is ignored: a directory that cannot be removed
        is disk, and disk must never cost a user their run.
        """
        try:
            root = slicer.app.temporaryPath
            for name in os.listdir(root):
                if not name.startswith(cls.TEST_FILE_DIR_KEY):
                    continue
                path = os.path.join(root, name)
                try:
                    if not os.path.isdir(path) or cls._ownerIsAlive(path):
                        continue
                    shutil.rmtree(path, ignore_errors=True)
                    logger.info("removed a leftover test-file directory: %s", path)
                except OSError:
                    continue
        except Exception:  # noqa: BLE001 - housekeeping, never fatal
            logger.debug("could not sweep leftover test files", exc_info=True)

    def _declaredKind(self, arg_name: str, name: str):
        """"file", "folder" or None - what the server said this entry is.

        Read on the MAIN thread and handed to the worker, never read from it:
        it comes off a widget, and a widget belongs to the thread that built
        it. None means the server published no `entries` at all, which is what
        `_hostedKind`'s fallback is for.
        """
        widget = self._inputWidgets.get(arg_name)
        reader = getattr(widget, "hosted_entries", None)
        for entry in (reader() if reader else []):
            if entry.get("name") == name:
                kind = entry.get("kind")
                return kind if kind in ("file", "folder") else None
        return None

    @classmethod
    def _ownerIsAlive(cls, path: str) -> bool:
        """Is the Slicer that created this directory still running?

        `os.kill(pid, 0)` asks the kernel and changes nothing. An unreadable or
        nonsensical mark counts as dead: the worst case is deleting a cohort
        someone would have re-downloaded, against a disk that fills up for good.
        """
        try:
            with open(os.path.join(path, cls.OWNER_FILE)) as handle:
                pid = int(handle.read().strip())
        except (OSError, ValueError):
            return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def _removeOwnTestFiles(self) -> None:
        """Drop this session's downloaded test files when the panel goes away.

        `cleanup()` runs when the widget is destroyed -- Slicer quitting, or a
        module reload -- so the path an input still holds is about to stop
        mattering. If it never runs (a crash), the next session's sweep finds
        the directory with a dead owner and removes it then. Two layers,
        because Slicer removes nothing on its own and a clinician has no reason
        to know /tmp exists.
        """
        root, self._testFileRoot = self._testFileRoot, None
        self._testFileCache.clear()
        if not root:
            return
        try:
            shutil.rmtree(root, ignore_errors=True)
            logger.info("removed this session's test-file directory: %s", root)
        except Exception:  # noqa: BLE001 - housekeeping, never fatal
            logger.debug("could not remove %s", root, exc_info=True)

    def _onHostedTestFile(self, arg_name: str, name: str) -> None:
        """Download one of the tool's server-hosted test files and use it.

        **On a BackgroundJob, always.** The panel used to reach a hosted test
        file by NAME, which cost nothing because the file never moved; it is
        fetched now, and fetching it on the main thread froze the whole Slicer
        window for as long as it took -- measured at 21 minutes once. The
        elapsed-time label is the same channel a tool run reports on.

        Cached by name for the session: a second pick of the same entry reuses
        what is on disk and issues no request at all, so flipping between two
        test cohorts is free after the first of each.
        """
        widget = self._inputWidgets.get(arg_name)
        if widget is None or not name:
            return

        cached = self._testFileCache.get(name)
        if cached and os.path.exists(cached):
            self._useTestFile(arg_name, name, cached)
            return

        if self._downloadJob is not None:
            slicer.util.showStatusMessage(
                _("Another test file is still downloading."), 5000
            )
            return

        destination = os.path.join(self._testFileDir(), _safe_name(name))
        if os.path.exists(destination):
            # A previous pick in this session that never made it into the
            # cache (a rebuilt panel, another argument offering the same file).
            self._useTestFile(arg_name, name, destination)
            return

        declared = self._declaredKind(arg_name, name)

        def task(progress_cb):
            return self._fetchTestFile(name, destination, declared, progress_cb)

        def finish():
            self._downloadJob = None
            self._hideProgress()

        def on_success(path):
            finish()
            self._useTestFile(arg_name, name, path)

        def on_error(exc):
            finish()
            slicer.util.errorDisplay(
                _("Could not download the test file: {error}").format(error=exc)
            )

        self._downloadJob = BackgroundJob(
            task, on_success=on_success, on_error=on_error, on_progress=self._showPhase
        )
        self._showPhase(_("Downloading {name}...").format(name=name))
        self._downloadJob.start()

    def _fetchTestFile(self, name: str, destination: str, declared, progress_cb) -> str:
        """Worker-thread half: download, unpack a hosted folder, move into place.

        Staged in a sibling directory and renamed at the end, so a failed or
        interrupted download can never leave a half-extracted folder that the
        existence check above would mistake for a completed one.
        """
        staging = destination + ".downloading"
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        # Timed per phase and logged. Outside Slicer this whole path measures
        # 0.22 s for a 94 MB file and 0.39 s for a 94 MB folder -- against
        # curl's 0.24 s -- while a user watching the panel reported more than
        # ten seconds. The difference is somewhere in here, and one log line
        # says where instead of inviting a guess.
        timings = []

        def phase(label, work):
            started = time.perf_counter()
            try:
                return work()
            finally:
                timings.append((label, time.perf_counter() - started))

        try:
            payload = os.path.join(staging, _safe_name(name))
            phase("download", lambda: self.client.download_testfile(
                self.TOOL_NAME, name, payload, progress_cb))

            # A hosted FOLDER is zipped by the server on the way out (there
            # being no other way to put a directory on a wire) and is unpacked
            # here, so the input points at a directory the tool can walk. A
            # hosted FILE that happens to be a .zip is left exactly as it is:
            # it is the file, and unpacking it would hand the tool something
            # the server never offered.
            if self._hostedKind(name, declared, payload) == "folder":
                progress_cb(_("Unpacking {name}...").format(name=name))
                unpacked = os.path.join(staging, "unpacked")
                phase("unpack", lambda: slicer_io.unzip_folder(payload, unpacked))
                phase("move", lambda: os.rename(unpacked, destination))
            else:
                phase("move", lambda: os.replace(payload, destination))
        finally:
            phase("clean", lambda: shutil.rmtree(staging, ignore_errors=True))
            # Kept on the instance so the main thread can SHOW it. A
            # `logger.info` alone does not reach Slicer's Python console, so
            # the measurement existed and nobody could read it -- the same
            # mistake as the server's peak VRAM, which was recorded on every
            # run and never read back.
            self._lastTestFileTimings = list(timings)
            logger.info(
                "test file %r: %s", name,
                ", ".join("{} {:.2f}s".format(label, seconds) for label, seconds in timings),
            )
        # Either way the answer is the same path: a file lands as itself and a
        # folder as its unpacked directory, so nothing downstream has to ask
        # which it was.
        return destination

    @staticmethod
    def _hostedKind(name: str, declared, payload: str) -> str:
        """"file" or "folder" for a hosted entry.

        The server says so outright when it publishes `entries`, which is the
        normal case and the only one worth trusting. An older server publishes
        names alone, and then the shape of what arrived has to answer: the
        endpoint zips a folder and streams a file untouched, and a directory
        name carries no extension -- so an extensionless name that really did
        arrive as an archive was a directory. The bytes are sniffed rather than
        the name, here and nowhere else: `is_extractable_archive` refuses to,
        because a RESULT .xlsx is a zip container and must not be unpacked, but
        that reasoning is exactly why the name alone cannot settle this one.
        """
        if declared in ("file", "folder"):
            return declared
        if not os.path.splitext(name)[1] and zipfile.is_zipfile(payload):
            return "folder"
        return "file"

    def _useTestFile(self, arg_name: str, name: str, path: str) -> None:
        """Point the input at the downloaded test file, and show it.

        Order matters: the path is written FIRST and the scene load is a
        courtesy after it. Loading is what the download is for -- a clinician
        asked for this scan so they could look at it beside the panel -- but a
        file Slicer's reader refuses is still a perfectly good input, so a
        failed load is a log line and the run goes ahead (slicer_io.load_input
        never raises).
        """
        load_started = time.perf_counter()
        self._testFileCache[name] = path
        widget = self._inputWidgets.get(arg_name)
        if widget is not None:
            formgen.set_local_path(widget, path)
        self._checkCanApply()

        # A FOLDER is deliberately never loaded: a forty-patient cohort would
        # put hundreds of nodes in the scene, which is worse than showing
        # nothing at all.
        loaded_into_scene = False
        if not os.path.isdir(path):
            # Said out loud, because this is the slow half and it does not look
            # like it: fetching a 94 MB scan takes 0.3 s over ranged parts,
            # then Slicer spends twenty seconds decompressing it and building
            # the image. A progress line still reading "Downloading..." while
            # that happens makes a fast transfer look like a stalled one.
            self._showPhase(_("Loading {name} into the scene...").format(name=name))
            slicer.app.processEvents()
            slicer_io.load_input(path)
            loaded_into_scene = True
        # The breakdown goes where a user can actually see it. "It took more
        # than ten seconds" is not a bug report anyone can act on; "download
        # 0.3s, unpack 1.2s, scene 8.4s" is.
        timings = list(getattr(self, "_lastTestFileTimings", []))
        if loaded_into_scene:
            # Only when there WAS one. A folder is never loaded, and reporting
            # `scene 0.0s` for it would name a phase that did not happen.
            timings.append(("scene", time.perf_counter() - load_started))
        total = sum(seconds for _label, seconds in timings)
        breakdown = ", ".join(
            "{} {:.1f}s".format(label, seconds) for label, seconds in timings
        )
        # LEFT on the panel, not hidden. `_hideProgress` used to run right
        # here, so the one line saying where the seconds went lived only in the
        # status bar for eight seconds -- which is no use to someone trying to
        # find out why a download felt slow. It stays until the next action.
        self._showPhase(
            _("{name} ready in {total:.1f}s -- {breakdown}").format(
                name=name, total=total, breakdown=breakdown
            )
        )
        slicer.util.showStatusMessage(
            _("Test file ready in {total:.1f}s ({breakdown}): {path}").format(
                total=total, breakdown=breakdown, path=path
            ),
            8000,
        )
        # `print` as well as the logger, deliberately. A module logger does not
        # reach Slicer's Python console, so the one measurement that answers
        # "why did that feel slow" was written where nobody could read it --
        # the same mistake as the server's peak VRAM, recorded on every run and
        # never read back. One line per download is not noise; it is the line
        # someone pastes into a bug report.
        summary = "[test file] {} ready in {:.1f}s -- {}".format(name, total, breakdown)
        print(summary)
        logger.info(summary)

    # ------------------------------------------------------------------
    # Server status banner
    # ------------------------------------------------------------------

    def _refreshServerStatus(self) -> None:
        """Keep the job on the instance, never in a local: a BackgroundJob is
        only kept alive by its own reference cycle (job -> QTimer -> bound
        _drain -> job), so a cyclic-GC pass — a module reload triggers one —
        can collect it mid-flight. Its timer dies with it, the callback never
        runs, and the badge stays stuck on "checking...". Owning it also lets
        cleanup() cancel it, so a job started by a widget Qt has since deleted
        can't write into a destroyed badge."""
        if self._statusJob:
            self._statusJob.cancel()

        def task(_progress_cb):
            return self.client.health()

        self._statusJob = BackgroundJob(
            task, on_success=self._onStatusChecked, on_error=lambda _exc: self._onStatusChecked(False)
        )
        self._statusJob.start()

    def _onStatusChecked(self, ok: bool) -> None:
        self._statusJob = None
        if self._statusBadge:
            design.update_status_badge(self._statusBadge, ok)

        # The panel is built from the schema, once. If the server was down when
        # this module was opened, all it holds is an error label — and the
        # health check coming back green is the one signal that it is worth
        # trying again. Without this the module stays broken for the whole
        # Slicer session, still showing a connection error against a server
        # that is now up.
        if ok and self._schemaError is not None and self.uiWidget:
            logger.info("Server is reachable again, rebuilding the panel for '%s'", self.TOOL_NAME)
            # force_refresh: the cached /tools may be exactly what is wrong
            # (fetched from another server, or before this tool was registered).
            self._buildForm(force_refresh=True)
