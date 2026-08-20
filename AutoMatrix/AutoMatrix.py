"""AutoMatrix - apply a registration matrix, computed on the tool server.

Replaces the former local module (a Slicer widget driving Automatrix_CLI, plus
a Mirror check box that downloaded a matrix from a GitHub release and typed its
path into the matrix field). Nothing is computed in Slicer any more: the panel
is generated from the server's `GET /tools` entry, the scans and the matrices
go up, the moved files come back. AutoMatrix_Method/ and Resources/UI/ are left
in the tree but are no longer wired to this module.

Three things about AutoMatrix's schema are worth knowing when reading this file:

* **There is no mode, so nothing is hidden.** Every argument is read on every
  run, which is why no `visible_when` appears anywhere in the schema and why
  there is no conditional code here. The sections separate the two paths a user
  must fill in from the things they only sometimes change.
* **The Mirror button is gone and the mirroring is not.** The server's tool
  still applies a matrix whose name contains "mirror" in the scan's own space,
  as upstream does; what has gone is the client downloading `Mirror.zip` to get
  hold of one. A mirroring matrix is now named like any other file.
* **A landmark file is a result too.** AutoMatrix moves `.mrk.json` control
  points as readily as it resamples a volume, so what comes back is a mixture
  and both kinds are offered to the scene.
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

logger = logging.getLogger("AutoMatrix")


class AutoMatrix(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("AutoMatrix")
        self.parent.categories = ["Automated Dental Tools"]
        self.parent.dependencies = ["ServerToolsCore"]
        self.parent.contributors = [
            "Gaelle Leroux (UoM)",
            "Lucia Cevidanes (UoM)",
            "Juan Carlos Prieto (UoNC)",
        ]
        self.parent.helpText = _("""
        Applies a registration matrix that AREG, ASO or a mirroring transform already
        produced, on the Automated Dental Tools server. Give it a folder of scans and a
        folder of matrices; each scan is paired with its own matrix by patient key, and
        the moved volumes and landmark files come back. Segmentations are resampled with
        nearest-neighbour so no label is invented.
        See more information in <a href="https://github.com/DCBIA-OrthoLab/SlicerAutomatedDentalTools">documentation</a>.
        """)
        self.parent.acknowledgementText = _("""
        This module was supported by NIDCR R01 024450.
        """)


class AutoMatrixWidget(ServerToolWidgetBase):
    """Thin GUI: HTTP, async, form generation, styling and lifecycle all live
    in ServerToolsCoreLib. See ARCHITECTURE.md."""

    TOOL_NAME = "AutoMatrix"

    # No FILE_INPUTS. `scans`, `matrices` and `reference` are a packaged tool's
    # `path`, and the client already gives that a picker taking a file OR a
    # folder (client.accepts_folder: "a packaged tool's 'path' always does").
    # All three genuinely take both -- a cohort is a folder, one case is a file,
    # and a single named matrix is deliberately applied to every patient, which
    # is how one mirroring transform serves a whole batch. Naming a mode here
    # would restate the schema at best and contradict it at worst.
    #
    # No RESULT_KIND: output_kind "files" is the input tree rebuilt, one moved
    # file per scan and matrix plus AutoMatrix_report.json, bundled into one
    # .zip and unpacked into the output folder the user picks.
    #
    # No TEST_DATA: the upstream module's only download is the Mirror matrix,
    # which is a matrix rather than test data and is now named like any other
    # file. There is no published scan/matrix pair to point at.

    # A cohort moved through four region matrices legitimately returns dozens
    # of files. Twelve is what AREG and GreedyReg use for the same kind of
    # output.
    MAX_RESULTS_TO_LOAD = 12

    # Pattern -> how to load it. A moved scan is a VOLUME whether or not it was
    # a segmentation: AutoMatrix resamples a label map, it does not create one,
    # and loading a mask as a segmentation node here would relabel a file the
    # user already has labelled. `AutoMatrix_report.json` is deliberately
    # absent, and so is `*.mrk.json`'s neighbour glob -- see _findResults.
    _LOADABLE = (
        ("*.nii.gz", "volume"),
        ("*.nii", "volume"),
        ("*.nrrd", "volume"),
        ("*.mrk.json", "markups"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loadResultsCheckBox = None

    # ------------------------------------------------------------------
    # Panel
    # ------------------------------------------------------------------

    def addExtraWidgets(self, layout) -> None:
        self._loadResultsCheckBox = qt.QCheckBox(
            _("Load the moved scans and landmarks into the scene when done"))
        layout.addWidget(self._loadResultsCheckBox)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def handleResult(self, result) -> None:
        """Unpack the archive (base class), then report and optionally load.

        The report is the half that is not visible on disk. A scan whose matrix
        never matched produces no file and no error, and the count of skipped
        pairings is the only thing that says so -- which is exactly the case
        where a user would otherwise conclude the tool had run on everything.
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

        Never fatal: the moved files are already on disk and are what the user
        asked for. A missing report costs them the summary, not the run.
        """
        found = glob.glob(os.path.join(outputDir, "**", "AutoMatrix_report.json"),
                          recursive=True)
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
        skipped = report.get("skipped") or 0
        if skipped:
            return _("AutoMatrix: {written} file(s) written, {skipped} pairing(s) "
                     "skipped - see AutoMatrix_report.json.").format(
                         written=report.get("written") or 0, skipped=skipped)
        return _("AutoMatrix: {written} file(s) written.").format(
            written=report.get("written") or 0)

    @classmethod
    def _findResults(cls, outputDir: str) -> list:
        """[(path, kind)] for every result with a loader.

        The report is a `.json` and not a `.mrk.json`, so the markups pattern
        never picks it up; it is named here only because the two extensions
        look alike enough to be worth saying once.
        """
        return sorted(
            (path, kind)
            for pattern, kind in cls._LOADABLE
            for path in glob.glob(os.path.join(outputDir, "**", pattern), recursive=True)
        )

    def _loadResults(self, outputDir: str) -> None:
        found = self._findResults(outputDir)
        if not found:
            slicer.util.showStatusMessage(_("AutoMatrix: no result file found to load."), 5000)
            return

        if len(found) > self.MAX_RESULTS_TO_LOAD:
            slicer.util.infoDisplay(
                _(
                    "{count} result files were produced - too many to load at once.\n"
                    "They are all saved in {path}."
                ).format(count=len(found), path=outputDir)
            )
            return

        failed = []
        for path, kind in found:
            try:
                # `markups` has no entry in slicer_io's loader table, the same
                # way it has none in ASO's: a markups node is not a result kind
                # the server can ask for, only a file this module recognises.
                if kind == "markups":
                    slicer.util.loadMarkups(path)
                else:
                    slicer_io.load_result(path, kind)
            except Exception as exc:  # one bad file must not lose the others
                failed.append(f"{os.path.basename(path)}: {exc}")

        if failed:
            slicer.util.errorDisplay(
                _("Some results could not be loaded:\n{details}").format(
                    details="\n".join(failed))
            )
