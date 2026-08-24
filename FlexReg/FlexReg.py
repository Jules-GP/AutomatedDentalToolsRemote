"""
FlexReg: build a registration patch on an intraoral arch, and register on it.

Two arches are aligned on a REGION the clinician chooses rather than on the whole
mesh, because teeth move between timepoints and the palate does not: registering
on everything drags the result toward whatever moved most.

Thin GUI over the remote `FlexReg` tool. The patch and the registration run on
the server, so nothing is installed into Slicer's interpreter -- the former
module shipped 191 lines of `install_pytorch.py` for exactly that reason, its
patch propagation calling `.cuda()` with no availability test and no device
argument.

What did NOT move to the server is the preview. Dragging a pad recomputes the
patch outline here, on this machine, in about 18 ms on a 294k-point arch: a round
trip per gesture is not a preview. It needs nothing but vtk, numpy and
matplotlib, all of which ship with Slicer. The server is asked once, when a
button is pressed, for the real patch -- geodesic propagation on the mesh rather
than a polygon fill, and what the registration actually runs on. What comes back
REPLACES the previewed fill, so what stays on screen at the end is the region
the ICP ran on rather than this module's approximation of it.

The panel is a pair, not a cohort: T1 and T2 are two model nodes of the scene,
shown side by side in two of three 3D views, each carrying its own patch. The
third stays empty until a registration comes back into it. The tool itself still
takes a folder -- a caller with forty patients sends one request -- but a folder
has no single outline to drag, so this panel does not offer one.

The five pads come from the schema. `run()` declares each corner as a pair of
floats and `layout.py` gives the axes their ranges and their end labels, so
`formgen` builds the joysticks with no code here. This module only wires them to
the preview.

Authors:
- Nathan Hutin (UoM)
- Luc Anchling (UoM)
"""

import glob
import os
import shutil
import tempfile

import qt
import slicer
from slicer.i18n import tr as _
from slicer.ScriptedLoadableModule import ScriptedLoadableModule

from ServerToolsCoreLib import slicer_io
from ServerToolsCoreLib.base_widget import ServerToolWidgetBase

# The corner pads, in the order the preview names them.
CORNERS = ("anterior_right", "anterior_left", "posterior_right", "posterior_left")

# The tooth number driving each corner, by argument name.
TEETH = {corner: "tooth_" + corner for corner in CORNERS}

# The two arches of a run, by the argument that carries each, and the view it
# owns. `reference` is T1 -- the timepoint the other is moved onto -- so it
# takes the left-hand view, where a reader starts.
SIDES = ("reference", "surfaces")
VIEW_TAG = {"reference": "1", "surfaces": "2"}
RESULT_VIEW_TAG = "3"
SIDE_LABEL = {"reference": _("T1"), "surfaces": _("T2")}

# Three 3D views, side by side. The id is this module's own; Slicer's built-in
# layouts stop well below it.
LAYOUT_ID = 501
LAYOUT_XML = """
<layout type="horizontal">
  <item>
    <view class="vtkMRMLViewNode" singletontag="1">
      <property name="viewlabel" action="default">T1</property>
    </view>
  </item>
  <item>
    <view class="vtkMRMLViewNode" singletontag="2">
      <property name="viewlabel" action="default">T2</property>
    </view>
  </item>
  <item>
    <view class="vtkMRMLViewNode" singletontag="3">
      <property name="viewlabel" action="default">REG</property>
    </view>
  </item>
</layout>
"""

# The panel's own overlay channel on a scan: it holds the approximation while a
# pad is being dragged and the server's real patch once a run comes back. Named
# APART from the `Butterfly` array the tool writes, and never overwriting it --
# a mesh that arrived carrying a patch still carries it when the panel is done.
PREVIEW_ARRAY = "ButterflyPreview"
# What the tool writes, read out of the files it returns. Never added to a node.
PATCH_ARRAY = "Butterfly"

# What the preview draws into the scene. Kept out of the saved scene: it is a
# working overlay, and a study reopened a year later should not carry one.
CONTOUR_NODE = "FlexReg patch outline {}"
RESULT_NODE = "FlexReg registered"


class FlexReg(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("FlexReg")
        self.parent.categories = ["Automated Dental Tools"]
        self.parent.dependencies = []
        self.parent.contributors = ["Nathan Hutin (UoM)", "Luc Anchling (UoM)"]
        self.parent.helpText = _(
            "Build a registration patch on an intraoral arch and register two "
            "timepoints on it. The patch and the registration run on the remote "
            "server; the outline you drag is previewed here."
        )


class FlexRegWidget(ServerToolWidgetBase):
    """The panel. Everything except the preview comes from the schema."""

    TOOL_NAME = "FlexReg"
    AUTO_UI = True

    # Both arches are nodes of the scene rather than paths: they have to be
    # displayed to be shaped, and the tool's `surfaces` argument accepting a
    # folder is what a cohort request uses, not what this panel offers.
    FILE_INPUTS = {"reference": "model_node", "surfaces": "model_node"}

    # A registered arch is a MODEL, not a segmentation: FlexReg moves a surface,
    # it does not label one. `*.tfm` is deliberately absent -- the transform
    # carries a measurement back onto the original acquisition, and loading it
    # into the scene applies nothing by itself.
    _LOADABLE = (
        ("*.vtk", "model"),
        ("*.vtp", "model"),
        ("*.stl", "model"),
    )
    MAX_RESULTS_TO_LOAD = 12
    RESULT_KIND = "save_as"

    def __init__(self, parent=None):
        super().__init__(parent)
        # Imported lazily: it pulls numpy, vtk and matplotlib. Slicer ships all
        # three, but a module that will not IMPORT takes its whole panel with
        # it, and the preview is the one thing here that can be done without.
        from FlexRegLib.butterfly_preview import ButterflyPreview

        # One per side. The two arches are the same mouth a year apart, so the
        # pads are shared, but the tooth centroids they are measured from are
        # each arch's own -- which is what makes the same pad values land on
        # the same anatomy rather than on the same coordinates.
        self._previews = {side: ButterflyPreview() for side in SIDES}
        self._previewSurfaces = {side: None for side in SIDES}
        self._contourNodes = {}
        # {node id: (active scalar name, scalar visibility)} as it was before
        # the preview painted over it, so a scan already coloured by its own
        # labels gets that colouring back.
        self._paintedNodes = {}
        self._previousLayout = None
        self._previewCheckBox = None
        self._previewStatus = None
        self._seeButton = None
        # Set for the duration of a "See" run: the directory it wrote into,
        # which this module owns and deletes once the result is in the scene.
        self._previewDir = None

    # -- the panel ----------------------------------------------------

    def addExtraWidgets(self, layout) -> None:
        """A switch, a status line and the "See" button, under the form.

        The switch defaults ON: watching the outline follow the pads is the
        whole point of having pads, and a refresh costs 18 ms.
        """
        self._previewCheckBox = qt.QCheckBox(_("Preview the patch while I drag"))
        self._previewCheckBox.checked = True
        self._previewCheckBox.toggled.connect(self._onPreviewToggled)
        layout.addWidget(self._previewCheckBox)

        self._previewStatus = qt.QLabel("")
        self._previewStatus.setWordWrap(True)
        self._previewStatus.setVisible(False)
        layout.addWidget(self._previewStatus)

        # The same remote run as Apply, with nowhere to keep it: the result
        # goes into the third view and the files are deleted. It is how a
        # patch is judged -- by the registration it produces -- before a folder
        # is chosen for anything.
        self._seeButton = qt.QPushButton(_("See the registration (nothing is saved)"))
        self._seeButton.toolTip = _(
            "Run the patch and the registration on the server and show the "
            "result in the third view. Nothing is written to disk."
        )
        self._seeButton.enabled = False
        self._seeButton.clicked.connect(self.onSeeButton)
        layout.addWidget(self._seeButton)

        # Wired here because the widgets are built by _buildForm and
        # _buildInputWidgets, which both run before addExtraWidgets.
        self._wirePreview()

    def _wirePreview(self) -> None:
        """Recompute on every value that moves the patch.

        The pads move the outline; the four tooth numbers move the centroids it
        is built from, which is the expensive path -- the cache has to go.
        """
        for name in CORNERS + ("shift",):
            widget = self._argWidgets.get(name)
            if widget is None:
                continue
            for box in (getattr(widget, "xBox", None), getattr(widget, "yBox", None)):
                if box is not None:
                    box.valueChanged.connect(self._onPatchChanged)

        for argument in TEETH.values():
            widget = self._argWidgets.get(argument)
            if widget is not None and hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self._onTeethChanged)

        for side in SIDES:
            widget = self._inputWidgets.get(side)
            if widget is not None and hasattr(widget, "currentNodeChanged"):
                widget.currentNodeChanged.connect(self._onArchChanged)

    def _checkCanApply(self, *args) -> None:
        super()._checkCanApply(*args)
        # Apply also waits for an output folder (RESULT_KIND is "save_as");
        # See writes nothing, so the two arches are all it needs.
        if self._seeButton is not None:
            self._seeButton.enabled = all(self._nodeFor(side) is not None for side in SIDES)

    # -- the three views ----------------------------------------------

    def enter(self) -> None:
        super().enter()
        self._installLayout()
        # Pins both arches to their views and redraws their patches.
        self._onArchChanged()

    def exit(self) -> None:
        # The overlays go with the panel: they are this module's working
        # drawing, and another module's view should not inherit it.
        self._clearPreview()
        self._restoreLayout()
        super().exit()

    def cleanup(self) -> None:
        self._clearPreview()
        self._restoreLayout()
        self._discardPreviewDir()
        super().cleanup()

    def onSceneEndClose(self, caller, event) -> None:
        # The nodes the overlays were built from are gone; the caches that
        # remember them have to go too.
        self._contourNodes = {}
        self._paintedNodes = {}
        for side in SIDES:
            self._previews[side].clear()
            self._previewSurfaces[side] = None
        super().onSceneEndClose(caller, event)

    def _installLayout(self) -> None:
        """Three 3D views, remembering what was on screen before."""
        manager = slicer.app.layoutManager()
        if manager is None:
            return
        node = manager.layoutLogic().GetLayoutNode()
        # Adding the same description twice is what a module reload does, and
        # Slicer refuses the second one loudly.
        if not node.IsLayoutDescription(LAYOUT_ID):
            node.AddLayoutDescription(LAYOUT_ID, LAYOUT_XML)
        if manager.layout != LAYOUT_ID:
            self._previousLayout = manager.layout
            manager.setLayout(LAYOUT_ID)

    def _restoreLayout(self) -> None:
        if self._previousLayout is None:
            return
        manager = slicer.app.layoutManager()
        if manager is not None and manager.layout == LAYOUT_ID:
            manager.setLayout(self._previousLayout)
        self._previousLayout = None

    def _viewNode(self, tag: str):
        return slicer.mrmlScene.GetSingletonNode(tag, "vtkMRMLViewNode")

    def _showIn(self, node, tags) -> None:
        """Restrict a node's display to the named views.

        An empty list means "every view", which is the default and exactly what
        must not happen here: T1 drawn over T2 in both windows is one arch too
        many in each.
        """
        if node is None:
            return
        display = node.GetDisplayNode()
        if display is None:
            node.CreateDefaultDisplayNodes()
            display = node.GetDisplayNode()
        if display is None:
            return
        views = [self._viewNode(tag) for tag in tags]
        display.SetViewNodeIDs([view.GetID() for view in views if view is not None])

    def _pinArches(self) -> None:
        for side in SIDES:
            self._showIn(self._nodeFor(side), [VIEW_TAG[side]])

    def _frame(self, tags) -> None:
        """Recentre the named views on what they now show.

        Two acquisitions a year apart do not sit at the same place in world
        space, and a view opens on the scene's bounds rather than on the one
        arch it was just restricted to -- so an arch lands off to the side of
        its own window, or out of it. Upstream fixed this by hardening a
        translation into the model, which moves the patient's data to suit the
        camera; the camera is the thing that should move.
        """
        manager = slicer.app.layoutManager()
        if manager is None:
            return
        for tag in tags:
            view = self._viewNode(tag)
            if view is None:
                continue
            for index in range(manager.threeDViewCount):
                widget = manager.threeDWidget(index)
                if widget is not None and widget.mrmlViewNode() is view:
                    widget.threeDView().resetFocalPoint()
                    break

    def _nodeFor(self, side: str):
        widget = self._inputWidgets.get(side)
        node = getattr(widget, "currentNode", None)
        return node() if callable(node) else None

    # -- the preview --------------------------------------------------

    def _onPreviewToggled(self, checked) -> None:
        if checked:
            self._onArchChanged()
        else:
            self._clearPreview()
            self._setPreviewStatus("")

    def _onArchChanged(self, *_args) -> None:
        """A different arch, so both the view binding and the cache are stale."""
        self._pinArches()
        self._frame([VIEW_TAG[side] for side in SIDES])
        self._onTeethChanged()

    def _onTeethChanged(self, *_args) -> None:
        """The teeth changed, so the cached centroids are stale."""
        for preview in self._previews.values():
            preview.clear()
        self._onPatchChanged()

    def _onPatchChanged(self, *_args) -> None:
        if self._previewCheckBox is None or not self._previewCheckBox.checked:
            return

        messages = []
        for side in SIDES:
            problem = self._refreshSide(side)
            if problem:
                messages.append("{}: {}".format(SIDE_LABEL[side], problem))
        self._setPreviewStatus("\n".join(messages))

    def _refreshSide(self, side: str):
        """Redraw one arch's patch. Returns what to say when it cannot be."""
        node = self._nodeFor(side)
        surface = node.GetPolyData() if node is not None else None
        if surface is None:
            self._clearSide(side)
            return _("pick a labelled arch to preview the patch.")

        preview = self._previews[side]
        teeth = self._selectedTeeth()
        if (not preview.ready
                or self._previewSurfaces[side] is not surface
                or not preview.matches(teeth)):
            self._previewSurfaces[side] = surface
            # Rebuilt only here: it walks every vertex and projects it, which is
            # the expensive half. Dragging a pad reuses it.
            preview.prepare(surface, teeth)

        if not preview.ready:
            self._clearSide(side)
            return preview.error or _("cannot preview this arch.")

        values = self._padValues()
        try:
            # with_fill=True: the outline says where the boundary is, the fill
            # says which side of it you are on, and on a curved palate the
            # second is not readable from the first. It is the approximate half
            # -- a point-in-polygon test where the server floods the mesh -- and
            # it is replaced by the server's own patch once a run comes back.
            contour, labels, _corners = preview.compute(
                values["ratios"], values["adjusts"], values["shift"], with_fill=True
            )
        except Exception as error:  # a preview must never take the panel down
            self._clearSide(side)
            return str(error)

        self._showContour(side, contour)
        self._paint(node, labels, PREVIEW_ARRAY)
        return None

    def _padValues(self) -> dict:
        """The five pads, named as the preview names them."""
        ratios, adjusts = {}, {}
        for corner in CORNERS:
            widget = self._argWidgets.get(corner)
            pair = widget.value() if widget is not None else [0.5, 0.0]
            ratios[corner], adjusts[corner] = float(pair[0]), float(pair[1])

        widget = self._argWidgets.get("shift")
        shift = widget.value() if widget is not None else [0.0, 0.0]
        return {
            "ratios": ratios,
            "adjusts": adjusts,
            "shift": (float(shift[0]), float(shift[1])),
        }

    def _selectedTeeth(self) -> dict:
        teeth = {}
        for corner, argument in TEETH.items():
            widget = self._argWidgets.get(argument)
            value = getattr(widget, "value", 0)
            teeth[corner] = int(value() if callable(value) else value)
        return teeth

    def _showContour(self, side: str, contour) -> None:
        node = self._contourNodes.get(side)
        if node is None or node.GetScene() is None:
            node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLModelNode", CONTOUR_NODE.format(SIDE_LABEL[side])
            )
            node.CreateDefaultDisplayNodes()
            display = node.GetDisplayNode()
            display.SetColor(1.0, 0.85, 0.1)
            display.SetLineWidth(3)
            display.SetScalarVisibility(False)
            # Flat: the outline is a drawing, and shading it makes the half
            # facing away from the light unreadable.
            display.SetLighting(False)
            # A working overlay, not a result: a study reopened a year later
            # should not carry one.
            node.SetSaveWithScene(False)
            self._contourNodes[side] = node
            # In its own arch's view only, so the third view stays a clean
            # comparison of two surfaces.
            self._showIn(node, [VIEW_TAG[side]])
        node.SetAndObservePolyData(contour)
        node.Modified()

    def _paint(self, node, labels, name: str) -> None:
        """Colour an arch by a per-point patch array, under `name`.

        The array is added to the user's own mesh, as upstream did: a patch IS
        an array on the mesh, and a copy of a 294k-point arch per drag is not a
        preview. What was displayed before is remembered so it can be handed
        back (see _unpaint).
        """
        polydata = node.GetPolyData()
        display = node.GetDisplayNode()
        if polydata is None or display is None:
            return

        from vtk.util.numpy_support import numpy_to_vtk

        if node.GetID() not in self._paintedNodes:
            self._paintedNodes[node.GetID()] = (
                display.GetActiveScalarName(), bool(display.GetScalarVisibility())
            )

        array = numpy_to_vtk(labels, deep=1)
        array.SetName(name)
        polydata.GetPointData().AddArray(array)

        display.SetActiveScalarName(name)
        display.SetScalarRangeFlag(slicer.vtkMRMLDisplayNode.UseManualScalarRange)
        display.SetScalarRange(0.0, 1.0)
        display.SetScalarVisibility(True)
        polydata.Modified()

    def _unpaint(self, node) -> None:
        """Hand the arch's own colouring back."""
        if node is None or node.GetScene() is None:
            return
        previous = self._paintedNodes.pop(node.GetID(), None)
        polydata = node.GetPolyData()
        # Only the overlay: a mesh that arrived carrying its own `Butterfly` --
        # the output of an earlier run, reopened -- keeps it.
        if polydata is not None and polydata.GetPointData().GetArray(PREVIEW_ARRAY):
            polydata.GetPointData().RemoveArray(PREVIEW_ARRAY)
            polydata.Modified()
        display = node.GetDisplayNode()
        if display is not None and previous is not None:
            display.SetActiveScalarName(previous[0] or "")
            display.SetScalarVisibility(previous[1])

    def _clearSide(self, side: str) -> None:
        node = self._contourNodes.pop(side, None)
        if node is not None and node.GetScene() is not None:
            slicer.mrmlScene.RemoveNode(node)
        self._unpaint(self._nodeFor(side))

    def _clearPreview(self) -> None:
        for side in SIDES:
            self._clearSide(side)
        # A node the dropdown no longer points at can still be painted -- the
        # user changed their selection while the overlay was on it.
        for nodeId in list(self._paintedNodes):
            self._unpaint(slicer.mrmlScene.GetNodeByID(nodeId))

    def _setPreviewStatus(self, message) -> None:
        if self._previewStatus is not None:
            self._previewStatus.setText(message)
            self._previewStatus.setVisible(bool(message))

    # -- running it ---------------------------------------------------

    def onSeeButton(self) -> None:
        """The same run as Apply, into a directory nobody keeps."""
        self._previewDir = tempfile.mkdtemp(prefix="FlexReg_see_")
        self.onApplyButton()
        if self._job is None:
            # onApplyButton refused before starting (an input could not be
            # prepared) and said why. Nothing will come back to clean this up.
            self._discardPreviewDir()

    def _discardPreviewDir(self) -> None:
        """Forget See's directory, whether or not a result ever arrived.

        A run that failed or was cancelled must not leave it set: the NEXT
        press of Apply reads it through outputDirectory() and would write the
        user's results into a temporary folder about to be deleted.
        """
        if self._previewDir:
            shutil.rmtree(self._previewDir, ignore_errors=True)
            self._previewDir = None

    def onCancelButton(self) -> None:
        self._discardPreviewDir()
        super().onCancelButton()

    def _onJobError(self, exc) -> None:
        self._discardPreviewDir()
        super()._onJobError(exc)

    def outputDirectory(self, workspace) -> str:
        if self._previewDir:
            return self._previewDir
        return super().outputDirectory(workspace)

    def handleResult(self, result) -> None:
        """Show what came back, then say what was kept.

        Both buttons land here. The difference is the directory: Apply's is the
        user's, and its files stay; See's belongs to this module and is deleted
        as soon as the scene holds what it needs.
        """
        previewDir = self._previewDir
        self._previewDir = None

        if previewDir is None:
            # Unpacks the archive into the output folder and says where.
            super().handleResult(result)
            directory = os.path.dirname(result.path)
        else:
            directory = self._unpackInto(result)

        try:
            self._paintTruePatch(directory)
            self._loadRegistered(directory)
        finally:
            if previewDir is not None:
                shutil.rmtree(previewDir, ignore_errors=True)

    def _unpackInto(self, result) -> str:
        """The directory the run's files ended up in, archive expanded."""
        directory = os.path.dirname(result.path)
        if slicer_io.is_extractable_archive(result.path):
            self._showPhase(_("Extracting results..."))
            slicer.app.processEvents()
            try:
                slicer_io.unzip_folder(result.path, directory)
            finally:
                self._hideProgress()
            os.remove(result.path)
        return directory

    def _patchOf(self, path: str):
        """The `Butterfly` array of a surface the server wrote, or None.

        Read straight off the file rather than by loading a node: the point
        order is the input's, so the array drops onto the arch already in the
        scene and nothing new is added to it.
        """
        import vtk
        from vtk.util.numpy_support import vtk_to_numpy

        reader = vtk.vtkPolyDataReader()
        reader.SetFileName(path)
        reader.Update()
        surface = reader.GetOutput()
        if surface is None or surface.GetNumberOfPoints() == 0:
            return None
        array = surface.GetPointData().GetArray(PATCH_ARRAY)
        return None if array is None else vtk_to_numpy(array).astype("float32")

    def _paintTruePatch(self, directory: str) -> None:
        """Replace the previewed fill with the one the registration ran on.

        The preview fills by testing which vertices project inside the outline;
        the server floods the mesh from the middle of the patch, bounded by the
        same curve. They agree except where the surface folds back on itself --
        and the whole method rests on the region, so the last thing on screen
        should be the region rather than this module's guess at it.
        """
        report = os.path.join(directory, "FlexReg_report.json")
        if not os.path.isfile(report):
            return
        import json

        try:
            written = json.loads(open(report, encoding="utf-8").read())
        except (OSError, ValueError):  # a report we cannot read costs the repaint, nothing else
            return

        produced = {"reference": written.get("reference")}
        surfaces = written.get("surfaces") or {}
        # One arch went up, so there is one entry; naming it by position rather
        # than by file name keeps this working when the server renames outputs.
        for entry in surfaces.values():
            if entry.get("status") == "ok":
                produced["surfaces"] = entry.get("output")
                break

        for side, path in produced.items():
            node = self._nodeFor(side)
            if not path or node is None or not os.path.isfile(path):
                continue
            labels = self._patchOf(path)
            if labels is not None and labels.size == node.GetPolyData().GetNumberOfPoints():
                self._paint(node, labels, PREVIEW_ARRAY)

    def _loadRegistered(self, directory: str) -> None:
        """Put the registered arch in the third view, beside the reference.

        The reference is added to that view rather than moved into it: judging
        a registration means looking at the two surfaces together, and T1 is
        still what the left-hand view is for.
        """
        found = sorted(
            path
            for pattern, _kind in self._LOADABLE
            for path in glob.glob(os.path.join(directory, "**", pattern), recursive=True)
            # The patched reference is already in the scene as the user's own
            # node; loading a second copy of it into the same view would draw
            # every triangle twice and look like a perfect registration.
            if not os.path.basename(path).endswith("_patch.vtk")
        )
        if not found:
            slicer.util.showStatusMessage(_("FlexReg: no registered surface came back."), 5000)
            return
        if len(found) > self.MAX_RESULTS_TO_LOAD:
            slicer.util.showStatusMessage(
                _("FlexReg: {count} surfaces came back, too many to show.").format(count=len(found)),
                5000,
            )
            return

        for node in self._resultNodes():
            slicer.mrmlScene.RemoveNode(node)

        failed = []
        for path in found:
            try:
                node = slicer_io.load_result(path, "model")
            except Exception as exc:  # one bad file must not lose the others
                failed.append("{}: {}".format(os.path.basename(path), exc))
                continue
            if node is None:
                continue
            node.SetName(RESULT_NODE)
            node.SetSaveWithScene(False)
            node.CreateDefaultDisplayNodes()
            display = node.GetDisplayNode()
            display.SetColor(0.35, 0.75, 1.0)
            display.SetScalarVisibility(False)
            self._showIn(node, [RESULT_VIEW_TAG])

        # T1 in the result view as well as its own, so the two are read
        # together. T2 is deliberately not: the point of the third view is the
        # MOVED arch against the reference.
        reference = self._nodeFor("reference")
        if reference is not None:
            self._showIn(reference, [VIEW_TAG["reference"], RESULT_VIEW_TAG])
        self._frame([RESULT_VIEW_TAG])

        if failed:
            slicer.util.errorDisplay(
                _("Some results could not be loaded:\n{details}").format(details="\n".join(failed))
            )

    def _resultNodes(self) -> list:
        """What a previous run left in the third view."""
        nodes = slicer.util.getNodesByClass("vtkMRMLModelNode")
        return [node for node in nodes if node.GetName() == RESULT_NODE]
