"""GreedyReg - Greedy affine registration, computed on the tool server.

Replaces the former local module (a Slicer widget driving GreedyReg_CLI, an
ITK-SNAP binary downloaded on first use, and ALI_CBCT run in-process for the
Distant Registration tab). Nothing is computed in Slicer any more: the panel is
generated from the server's `GET /tools` entry, the scans go up, the registered
volumes come back. GreedyReg_Method/ is left in the tree but is no longer wired
to this module.

Three things about GreedyReg's schema are worth knowing when reading this file:

* **`mode` is what the two upstream tabs became.** Automatic Registration is
  `Greedy`, Distant Registration is `Landmark`, and running one after the other
  - which the old panel told you to do by hand, in a status message - is
  `Landmark + Greedy`. Every argument that belongs to one mode carries a
  `visible_when` on it, so the panel hides what the chosen mode never reads.
  That is the schema's doing; there is no code here for it.
* **The Greedy binary is gone from the client's life.** Upstream downloaded a
  60 MB ITK-SNAP archive (or ran its Windows installer silently) the first time
  you pressed Register, and the "Download Greedy" button existed for when that
  failed. The server's tool depends on the `picsl-greedy` wheel, so there is
  nothing to fetch and no button.
* **Landmark modes cost a second tool.** The server reaches ALI_CBCT through
  its supervisor; the client neither knows nor arranges that. What it means
  here is only that those modes take longer and need a model bundle named.
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

logger = logging.getLogger("GreedyReg")


class GreedyReg(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("GreedyReg")
        self.parent.categories = ["Automated Dental Tools"]
        self.parent.dependencies = ["ServerToolsCore"]
        self.parent.contributors = ["Lucia Cevidanes (UoM)", "Juan Carlos Prieto (UoNC)"]
        self.parent.helpText = _("""
        Registers a follow-up CBCT onto its baseline, on the Automated Dental Tools server.
        Give it two scans or two folders of them, paired by patient key; the server runs
        Greedy's affine registration and returns each registered volume with the transform
        that produced it. When the two timepoints are too far apart for Greedy to find
        anything, the landmark modes bring them together first.
        See more information in <a href="https://github.com/DCBIA-OrthoLab/SlicerAutomatedDentalTools">documentation</a>.
        """)
        self.parent.acknowledgementText = _("""
        Greedy is developed by the Penn Image Computing and Science Laboratory. This module
        was supported by NIDCR R01 024450.
        """)


class GreedyRegWidget(ServerToolWidgetBase):
    """Thin GUI: HTTP, async, form generation, styling and lifecycle all live
    in ServerToolsCoreLib. See ARCHITECTURE.md."""

    TOOL_NAME = "GreedyReg"

    # No FILE_INPUTS. Every input here is a packaged tool's `path`, and the
    # client already gives that a picker taking a file OR a folder
    # (client.accepts_folder: "a packaged tool's 'path' always does"). Naming a
    # mode here would only restate what the schema says, and could contradict
    # it: `t1`/`t2` really do take both -- two folders is a batch, two files is
    # one pair -- and so do `masks` and `init`.
    #
    # `landmark_model` is a path too, and will stop being one the day the
    # deployment marks it server_selectable="model": it is then published as a
    # NAME chosen from what the server hosts, and the client renders that with
    # no change here either. Nothing in this file has to know which it is.

    # No TEST_DATA: the upstream module ships no test-file button for
    # registration, and there is no published T1/T2 pair to point at. The
    # server-hosted testfiles dropdown still appears on any argument the
    # deployment marks selectable.

    # No RESULT_KIND: output_kind "files" is one registered volume and its
    # transform per pair, plus GreedyReg_report.json, bundled into one .zip and
    # unpacked into the output folder the user picks.

    # A cohort registered in one call legitimately returns dozens of volumes,
    # and loading them all would be worse than useless. Twelve is AREG's
    # number, and this returns the same kind of thing.
    MAX_RESULTS_TO_LOAD = 12

    # Pattern -> how to load it. A registered CBCT is a VOLUME: GreedyReg moves
    # a scan, it does not label one. `*_warp.mat` is deliberately absent -- the
    # transform is there to carry a measurement back onto the original
    # acquisition, and loading it into the scene applies nothing by itself.
    _LOADABLE = (
        ("*.nii.gz", "volume"),
        ("*.nii", "volume"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loadResultsCheckBox = None

    # ------------------------------------------------------------------
    # Panel
    # ------------------------------------------------------------------

    def addExtraWidgets(self, layout) -> None:
        self._loadResultsCheckBox = qt.QCheckBox(
            _("Load the registered volumes into the scene when done"))
        layout.addWidget(self._loadResultsCheckBox)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def handleResult(self, result) -> None:
        """Unpack the archive (base class), then report and optionally load.

        The report is the half that is not visible on disk: a pair that was
        never matched produces no file and no error, so the count of cases is
        the only thing that says whether the batch did what was asked.
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

        Never fatal: the registered volumes are already on disk and are what the
        user asked for. A missing report costs them the summary, not the run.
        """
        found = glob.glob(os.path.join(outputDir, "**", "GreedyReg_report.json"), recursive=True)
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
        cases = report.get("cases") or []
        return _("GreedyReg finished in {mode} mode: {count} pair(s) registered.").format(
            mode=report.get("mode") or "?", count=len(cases)
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
            slicer.util.showStatusMessage(_("GreedyReg: no result file found to load."), 5000)
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
                slicer_io.load_result(path, kind)
            except Exception as exc:  # one bad file must not lose the others
                failed.append(f"{os.path.basename(path)}: {exc}")

        if failed:
            slicer.util.errorDisplay(
                _("Some results could not be loaded:\n{details}").format(
                    details="\n".join(failed))
            )
