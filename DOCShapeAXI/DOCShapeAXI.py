"""DOCShapeAXI - surface grading, computed on the tool server.

Replaces the former local module (a Slicer widget driving DOCShapeAXI_CLI, a
conda `shapeaxi` environment, a WSL detour on Windows, and a checkpoint
downloaded from a GitHub release on every run). Nothing is computed in Slicer
any more: the panel is generated from the server's `GET /tools` entry, the
surfaces go up, the grades and the GradCAM surfaces come back.
DOCShapeAXI_utils/ is left in the tree but is no longer wired to this module.

Two things about DOCShapeAXI's schema are worth knowing when reading this file:

* **`task` narrows itself to the anatomy.** Only the nasopharynx airway has a
  model for each of binary, severity and regression; the mandibular condyle and
  the alveolar cleft have one four-grade model each. That is an `options_when`
  rule in the schema, so the combo box re-populates itself when the anatomy
  changes and there is no code for it here. The old panel offered all three
  and reached the four-class model regardless.
* **The result is two things, and the second is the point.** A prediction
  table, and one copy of each surface carrying a GradCAM array per grade. The
  arrays are what let a clinician see WHERE the model was looking, so the
  surfaces are what this module offers to load into the scene - not the table.
"""

import glob
import json
import logging
import os

import qt
import slicer
from slicer.i18n import tr as _
from slicer.ScriptedLoadableModule import ScriptedLoadableModule

from ServerToolsCoreLib import slicer_io
from ServerToolsCoreLib.base_widget import ServerToolWidgetBase

logger = logging.getLogger("DOCShapeAXI")


class DOCShapeAXI(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("DOCShapeAXI")
        self.parent.categories = ["Automated Dental Tools"]
        self.parent.dependencies = ["ServerToolsCore"]
        self.parent.contributors = [
            "Lucie Dole (UoNC)",
            "Lucia Cevidanes (UoM)",
            "Juan Carlos Prieto (UoNC)",
        ]
        self.parent.helpText = _("""
        Grades a surface mesh - a mandibular condyle, a nasopharynx airway or an alveolar
        cleft - with a shapeaxi classifier hosted on the Automated Dental Tools server, and
        returns each surface again carrying a GradCAM array that shows which part of it the
        model graded on.
        See more information in <a href="https://github.com/DCBIA-OrthoLab/SlicerAutomatedDentalTools">documentation</a>.
        """)
        self.parent.acknowledgementText = _("""
        This module was supported by NIDCR R01 024450.
        """)


class DOCShapeAXIWidget(ServerToolWidgetBase):
    """Thin GUI: HTTP, async, form generation, styling and lifecycle all live
    in ServerToolsCoreLib. See ARCHITECTURE.md."""

    TOOL_NAME = "DOCShapeAXI"

    # No FILE_INPUTS: `meshes` and `model` are a packaged tool's `path`, which
    # the client already gives a picker taking a file or a folder
    # (client.accepts_folder). A cohort is a folder and a single case is a
    # file, and both are what the tool takes.
    #
    # No RESULT_KIND: output_kind "files" is a prediction table plus one
    # GradCAM surface per case, bundled into one .zip and unpacked into the
    # output folder the user picks.
    #
    # No TEST_DATA: upstream ships no test-file button for this module.

    # A graded cohort returns one surface per case per grade. Twelve is what
    # AREG and GreedyReg use for the same kind of output.
    MAX_RESULTS_TO_LOAD = 12

    # Pattern -> how to load it. The GradCAM surfaces are models; the
    # prediction table is a CSV and there is nothing to put in a scene for it,
    # which is why it is deliberately absent here and named in the summary
    # instead.
    _LOADABLE = (
        ("*.vtk", "model"),
        ("*.vtp", "model"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loadResultsCheckBox = None

    # ------------------------------------------------------------------
    # Panel
    # ------------------------------------------------------------------

    def addExtraWidgets(self, layout) -> None:
        self._loadResultsCheckBox = qt.QCheckBox(
            _("Load the explainability surfaces into the scene when done"))
        self._loadResultsCheckBox.setChecked(True)
        layout.addWidget(self._loadResultsCheckBox)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def handleResult(self, result) -> None:
        """Unpack the archive (base class), then say what was graded with what.

        Which checkpoint produced a grade is worth stating: the anatomy and the
        task pick it, and a clinician reading a four-grade score needs to know
        it was not the binary model that gave it.
        """
        super().handleResult(result)

        outputDir = self._outputFolderWidget.currentPath if self._outputFolderWidget else None
        if not outputDir:
            return

        report = self._readRunReport(outputDir)
        if report:
            slicer.util.showStatusMessage(self._summarize(report), 8000)

        if self._loadResultsCheckBox and self._loadResultsCheckBox.isChecked():
            self._loadResults(outputDir)

    @staticmethod
    def _readRunReport(outputDir: str):
        """The run report, or None when there isn't a readable one.

        Never fatal: the grades are already on disk and are what the user asked
        for. A missing report costs them the summary, not the run.
        """
        found = glob.glob(
            os.path.join(outputDir, "**", "DOCShapeAXI_report.json"), recursive=True)
        if not found:
            return None
        try:
            with open(found[0], encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s: %s", found[0], exc)
            return None

    @staticmethod
    def _summarize(report: dict) -> str:
        return _(
            "DOCShapeAXI graded {surfaces} surface(s): {data_type}, {task}, "
            "using {checkpoint}."
        ).format(
            surfaces=report.get("surfaces", "?"),
            data_type=report.get("data_type") or "?",
            task=report.get("task") or "?",
            checkpoint=report.get("checkpoint") or "?",
        )

    @classmethod
    def _findResults(cls, outputDir: str) -> list:
        """[(path, kind)] for every result with a loader."""
        return sorted(
            (path, kind)
            for pattern, kind in cls._LOADABLE
            for path in glob.glob(os.path.join(outputDir, "**", pattern), recursive=True)
        )

    def _loadResults(self, outputDir: str) -> None:
        found = self._findResults(outputDir)
        if not found:
            slicer.util.showStatusMessage(
                _("DOCShapeAXI: no explainability surface found to load."), 5000)
            return

        if len(found) > self.MAX_RESULTS_TO_LOAD:
            slicer.util.infoDisplay(
                _(
                    "{count} surfaces were produced - too many to load at once.\n"
                    "They are all saved in {path}."
                ).format(count=len(found), path=outputDir)
            )
            return

        failed = []
        for path, kind in found:
            try:
                slicer_io.load_result(path, kind)
            except Exception as exc:  # one bad file must not lose the others
                failed.append(f"{os.path.basename(path)}: {exc}")

        if failed:
            slicer.util.errorDisplay(
                _("Some results could not be loaded:\n{details}").format(
                    details="\n".join(failed))
            )
