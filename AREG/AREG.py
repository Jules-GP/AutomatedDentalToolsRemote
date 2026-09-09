"""
Automated REGistration (AREG) of two timepoints of the same patient, for
cone-beam computed tomography (CBCT) and intraoral surface scans (IOS).

Thin GUI over the remote `AREG` tool. The registration runs on the server, so
nothing is installed into Slicer's interpreter, no conda environment is created,
and neither the segmentation models nor the registration checkpoint are ever
downloaded to this machine.

Replaces the former local module, which drove the AREG_CBCT / AREG_IOS /
AREG_IOSCBCT CLIs from inside Slicer and chained them with AMASSS, ASO, ALI and
CrownSeg by hand. `AREG_Method/` and `Resources/UI/AREG.ui` are left in the tree
but are no longer wired to this one.

Authors:
- Nathan Hutin (UoM)
- Luc Anchling (UoM)
"""

import glob
import os

import qt
import slicer
from slicer.i18n import tr as _
from slicer.ScriptedLoadableModule import ScriptedLoadableModule

from ServerToolsCoreLib import slicer_io
from ServerToolsCoreLib.base_widget import ServerToolWidgetBase


class AREG(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("AREG")
        self.parent.categories = ["Automated Dental Tools"]
        self.parent.dependencies = ["ServerToolsCore"]
        self.parent.contributors = [
            "Nathan Hutin (UoM)",
            "Luc Anchling (UoM)",
            "Lucia Cevidanes (UoM)",
            "Juan Carlos Prieto (UoNC)",
        ]
        self.parent.helpText = _("""
        Registers a follow-up scan onto a baseline scan of the same patient, remotely, on the
        Automated Dental Tools server. Give it a T1 folder and a T2 folder; each pair comes back
        with the T2 moved into the T1's frame and the transform that moved it, so growth or
        treatment change can be measured.
        CBCT registration is confined to the anatomy you pick (cranial base, mandible, maxilla);
        intraoral registration uses the palate, which orthodontic treatment does not move.
        See more information in <a href="https://github.com/DCBIA-OrthoLab/SlicerAutomatedDentalTools">documentation</a>.
        """)
        self.parent.acknowledgementText = _("""
        This file was developed by Nathan Hutin (UoM) and Luc Anchling (UoM) and was supported by
        NIDCR R01 024450, AA0F Dewel Memorial Biomedical Research award and by Research
        Enhancement Award Activity 141 from the University of the Pacific,
        Arthur A. Dugoni School of Dentistry.
        """)


class AREGWidget(ServerToolWidgetBase):
    """Thin GUI: everything else (HTTP, async, form generation, styling, lifecycle)
    lives in ServerToolsCoreLib. See ARCHITECTURE.md.

    Nothing about the pipeline lives here. Which anatomical regions can be
    registered on, which modes exist, what each mode additionally needs, and the
    fact that a CBCT run is never asked for an intraoral checkpoint, are all in
    the server's schema and rendered by formgen — so a region added server-side
    appears in this panel with no client release.

    The old local module spent 2574 lines on the same panel, plus three
    `AREG_Method` classes whose job was to build the argument dictionaries for
    five different CLIs and run them in sequence. That sequencing is the
    server's now: AREG calls AMASSS, ASO and CrownSeg in-process, so this module
    makes ONE request where the old one launched up to six processes and polled
    a log file for progress.

    In particular the mode is *asked for*, never guessed: a `.zip` can hold CBCT
    volumes or intraoral meshes, so no extension could tell this module which
    pipeline the user wanted. The same goes for `ios_patch`, which picks the
    region the two timepoints are aligned on and, with it, the arch: the palate
    (predicted, upper) or the mucogingival line (built from landmarks, lower).
    The panel shows the checkpoint for one and the landmark folder for the
    other, and that switch is `visible_when` in the schema, not code here.
    """

    TOOL_NAME = "AREG"
    # No FILE_INPUTS: the schema types `t1`, `t2` and `t1_masks` as
    # ["folder", "zip_file"], so "auto" already gives each one a picker that
    # takes a folder and zips it before upload. `t1`/`t2` are also flagged
    # server_selectable, which puts the hosted test cohorts above that picker.
    # Nothing here names a type, an extension, or a mode.
    #
    # No RESULT_KIND either: output_kind "files" is one registered scan and its
    # transform per case and per region, plus AREG_report.json, bundled into one
    # .zip and unpacked into the output folder the user picks.
    AUTO_UI = True

    # Loading is a courtesy for the single-pair run: a cohort registered on
    # three regions legitimately returns hundreds of files, and loading them all
    # would be worse than useless.
    MAX_RESULTS_TO_LOAD = 12
    # Pattern -> how to load it. A registered CBCT is a VOLUME, not a
    # segmentation: AREG moves a scan, it does not label one. `*.tfm` is
    # deliberately absent — the transform is there to carry a measurement back
    # onto the original acquisition, and loading it into the scene applies
    # nothing by itself.
    _LOADABLE = (
        ("*.nii.gz", "volume"),
        ("*.nii", "volume"),
        ("*.nrrd.gz", "volume"),
        ("*.nrrd", "volume"),
        ("*.gipl.gz", "volume"),
        ("*.gipl", "volume"),
        ("*.vtk", "model"),
        ("*.vtp", "model"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loadResultsCheckBox = None

    def addExtraWidgets(self, layout) -> None:
        self._loadResultsCheckBox = qt.QCheckBox(_("Load the results into the scene when done"))
        layout.addWidget(self._loadResultsCheckBox)

    def handleResult(self, result) -> None:
        """Unpack the archive (base class), then optionally load what it held."""
        super().handleResult(result)

        if not (self._loadResultsCheckBox and self._loadResultsCheckBox.isChecked()):
            return
        outputDir = self._outputFolderWidget.currentPath if self._outputFolderWidget else None
        if outputDir:
            self._loadResults(outputDir)

    @classmethod
    def _findResults(cls, outputDir: str) -> list:
        """[(path, kind)] for every result with a loader, in the per-region,
        per-case tree the server built."""
        return sorted(
            (path, kind)
            for pattern, kind in cls._LOADABLE
            for path in glob.glob(os.path.join(outputDir, "**", pattern), recursive=True)
        )

    def _loadResults(self, outputDir: str) -> None:
        found = self._findResults(outputDir)
        if not found:
            slicer.util.showStatusMessage(_("AREG: no result file found to load."), 5000)
            return

        if len(found) > self.MAX_RESULTS_TO_LOAD:
            slicer.util.infoDisplay(
                _(
                    "{count} result files were produced — too many to load at once.\n"
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
                _("Some results could not be loaded:\n{details}").format(details="\n".join(failed))
            )
