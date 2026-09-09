"""What happens when a user picks one of a tool's server-hosted test files.

    python3 -m unittest test_hosted_test_files

This is the base_widget half of the mechanism that replaced two contradicting
ones. A tool's test data used to be reachable two ways: the input row's
dropdown, which sent the hosted NAME and left the file on the server, and a
separate "Test data" button four modules declared by hand with a hardcoded
GitHub release URL. Only one of them put the scan where a clinician could open
it beside the panel, and neither knew about the other.

There is one now, it downloads, and the properties that make it usable are all
here: it never blocks the Slicer window, it fetches a given file once per
session, a hosted folder arrives as a .zip and is unpacked, a single file is
shown in the scene and a cohort deliberately is not, and a file the scene
refuses is still a perfectly good input.

`qt`/`ctk`/`slicer` are the stand-ins in qt_stubs.py, plus the few `slicer.util`
functions this path touches. `BackgroundJob` is replaced by a stand-in that
runs the task on a REAL worker thread and delivers it when the test says so -
the point being to prove the work leaves the main thread, which a synchronous
fake could not.
"""

import os
import shutil
import sys
import tempfile
import time
import threading
import types
import unittest
import zipfile

_HERE = os.path.abspath(os.path.dirname(__file__))
_CORE = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _HERE)
sys.path.insert(0, _CORE)

import qt_stubs  # noqa: E402

qt, ctk = qt_stubs.install()


def _stub_slicer():
    """The `slicer` surface base_widget and slicer_io touch on this path."""
    slicer = sys.modules["slicer"]

    i18n = types.ModuleType("slicer.i18n")
    i18n.tr = lambda text: text
    sys.modules["slicer.i18n"] = i18n
    slicer.i18n = i18n

    framework = types.ModuleType("slicer.ScriptedLoadableModule")

    class ScriptedLoadableModuleWidget:
        def __init__(self, parent=None):
            pass

    framework.ScriptedLoadableModuleWidget = ScriptedLoadableModuleWidget
    sys.modules["slicer.ScriptedLoadableModule"] = framework
    slicer.ScriptedLoadableModule = framework

    # `processEvents` so the "Loading ... into the scene" line is painted
    # BEFORE the load blocks the main thread. Without it the panel still reads
    # "Downloading", and a 0.3 s transfer followed by twenty seconds of
    # decompression looks like a stalled download -- which is what a user
    # reported.
    class _App:
        def __init__(self):
            self.processed = 0
            # Where leftovers from earlier sessions would be found.
            self.temporaryPath = tempfile.mkdtemp(prefix="slicer_temp_root_")

        def processEvents(self):
            self.processed += 1

    slicer.app = _App()

    util = types.ModuleType("slicer.util")

    class VTKObservationMixin:
        def __init__(self, *args, **kwargs):
            pass

        def removeObservers(self, *args, **kwargs):
            """`cleanup()` calls it; the stub has no scene to observe."""

    util.VTKObservationMixin = VTKObservationMixin
    # `key` is how the module names its own directories so it can sweep its
    # own leftovers without touching another module's.
    util.tempDirectory = lambda key="__SlicerTemp__", **kwargs: tempfile.mkdtemp(
        prefix=key + "_"
    )
    # Kept, because it is where the timing breakdown a user reads ends up.
    util.status_messages = []
    util.showStatusMessage = lambda message, *args, **kwargs: (
        util.status_messages.append(message)
    )
    util.errorDisplay = lambda *args, **kwargs: None
    util.loaded = []          # what a test asserts the scene received
    util.load_failures = set()  # paths the stubbed readers refuse

    def _loader(kind):
        def load(path):
            if path in util.load_failures:
                raise RuntimeError(f"unreadable {kind}")
            util.loaded.append((kind, path))
            return object()
        return load

    util.loadVolume = _loader("volume")
    util.loadModel = _loader("model")
    util.loadSegmentation = _loader("segmentation")
    util.loadLabelVolume = _loader("labelmap")
    util.loadTransform = _loader("transform")
    sys.modules["slicer.util"] = util
    slicer.util = util
    return util


_util = _stub_slicer()

from ServerToolsCoreLib import base_widget, formgen  # noqa: E402
from ServerToolsCoreLib.base_widget import ServerToolWidgetBase  # noqa: E402
from ServerToolsCoreLib.errors import ServerToolError  # noqa: E402


# One argument, typed the way a packaged tool types a scan-or-cohort input and
# flagged with the only value that makes its test files downloadable.
SCHEMA = {
    "name": "AREG",
    "output_kind": "files",
    "arguments": {
        "t1": {
            "type": "path",
            "types": ["path", "folder"],
            "required": True,
            "label": "T1",
            "description": "",
            "server_selectable": "testfile",
            "choices": None,
            "initial": None,
            "extensions": {"path": [".nii.gz", ".vtk", ".zip"]},
            "section": "Inputs",
            "visible_when": None,
            "ui": None,
            "groups": None,
        },
    },
}


class _Job:
    """Stand-in for BackgroundJob: a REAL worker thread, delivered on demand.

    `deliver()` is what the QTimer drain does on the main thread, so a test can
    look at the panel while the download is still in flight - which is the
    whole property under test.
    """

    started = []

    def __init__(self, target, on_success=None, on_error=None, on_progress=None):
        self._target = target
        self._on_success = on_success
        self._on_error = on_error
        self._on_progress = on_progress
        self._thread = None
        self._outcome = None
        self.worker_thread = None
        self.progress = []

    def start(self):
        _Job.started.append(self)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        pass

    def _run(self):
        self.worker_thread = threading.current_thread()
        try:
            self._outcome = ("success", self._target(self.progress.append))
        except Exception as exc:  # noqa: BLE001 - mirrors BackgroundJob
            self._outcome = ("error", exc)

    def deliver(self):
        self._thread.join(10)
        kind, payload = self._outcome
        if kind == "success" and self._on_success:
            self._on_success(payload)
        elif kind == "error" and self._on_error:
            self._on_error(payload)


class _FakeClient:
    """Answers `download_testfile` from a table of payloads, recording every
    call and the thread it arrived on."""

    def __init__(self):
        self.payloads = {}   # {hosted name: bytes | {member: bytes} for a folder}
        self.calls = []
        self.threads = []
        self.gate = None     # an Event a test holds to keep a download running
        self.error = None

    def download_testfile(self, tool_name, filename, destination, progress_cb=None):
        self.calls.append((tool_name, filename))
        self.threads.append(threading.current_thread())
        if self.gate is not None:
            self.gate.wait(10)
        if self.error is not None:
            raise self.error
        if progress_cb:
            progress_cb(f"Downloading {filename}... 100%")
        payload = self.payloads[filename]
        if isinstance(payload, dict):
            with zipfile.ZipFile(destination, "w") as archive:
                for member, content in payload.items():
                    archive.writestr(member, content)
        else:
            with open(destination, "wb") as handle:
                handle.write(payload)
        return destination

    def list_tool_data(self, _tool_name):
        return {"models": [], "testfiles": [], "entries": {}}


class HostedTestFileTest(unittest.TestCase):
    def setUp(self):
        _Job.started = []
        _util.loaded = []
        _util.load_failures = set()
        self._real_job = base_widget.BackgroundJob
        base_widget.BackgroundJob = _Job
        self.addCleanup(setattr, base_widget, "BackgroundJob", self._real_job)

        self.client = _FakeClient()
        self.panel = self._panel()
        self.addCleanup(self._cleanupDownloads)

    def _cleanupDownloads(self):
        if self.panel._testFileRoot:
            shutil.rmtree(self.panel._testFileRoot, ignore_errors=True)

    def _panel(self):
        panel = ServerToolWidgetBase.__new__(ServerToolWidgetBase)
        panel.TOOL_NAME = "AREG"
        panel.client = self.client
        panel._schema = SCHEMA
        panel._argWidgets = {}
        panel._sectionLayouts = {}
        panel._rows = {}
        panel._rowSections = {}
        panel._hiddenArgs = set()
        panel._sceneVolumes = {}
        panel._downloadJob = None
        # The rest of what a real __init__ sets and `cleanup()` reads. The
        # fixture builds the panel piecemeal; anything cleanup() touches has to
        # exist or the test fails for a reason that is not the subject.
        panel._runs = []
        panel._runsStarted = 0
        panel._statusJob = None
        panel._elapsedTimer = None
        panel._testFileRoot = None
        panel._testFileCache = {}
        panel._progressLabel = None
        # What the panel told the user, in order. `_showPhase` is the one
        # channel a run and a download share.
        panel.phases = []
        panel._showPhase = panel.phases.append
        panel.applyButton = None
        panel._outputFolderWidget = None
        # The real build, so the callback wiring under test is the shipped one.
        panel._inputWidgets = panel._buildInputWidgets(qt.QFormLayout())
        return panel

    def _offer(self, *entries):
        """Publish these test files on the argument, the way the panel does
        from GET /tools/{tool}/data."""
        data = {
            "models": [],
            "testfiles": [entry["name"] for entry in entries],
            "entries": {"testfiles": list(entries)},
        }
        self.panel._fillServerSelectable("t1", "testfile", data)

    @property
    def _row(self):
        return self.panel._inputWidgets["t1"]

    def _pick(self, name):
        """Select a hosted entry through the combo box, as a user does."""
        labels = [self._row.combo.itemText(i) for i in range(self._row.combo.count)]
        index = next(i for i, label in enumerate(labels) if label.startswith(name))
        self._row.combo.setCurrentIndex(index)

    # -- the picker -----------------------------------------------------

    def test_the_picker_lists_each_test_file_with_its_kind_and_size(self):
        self._offer(
            {"name": "CBCT_FullyAuto", "kind": "folder", "size": 355640000},
            {"name": "MG_test_scan.nii.gz", "kind": "file", "size": 94 * 1024 * 1024},
        )

        combo = self._row.combo
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count)],
            [
                # Names what the list holds; the path field beside it keeps
                # its own words (see ServerFileInput.CHOOSE_OPTION).
                formgen.ServerFileInput.PROMPT_HOSTED,
                "CBCT_FullyAuto  (folder, 339 MB)",
                "MG_test_scan.nii.gz  (file, 94 MB)",
            ],
        )

    def test_a_size_the_server_could_not_state_shows_nothing(self):
        """A backend that cannot size a tree cheaply sends null, and "0 B"
        would be a claim the server never made."""
        self._offer({"name": "cohort", "kind": "folder", "size": None})

        self.assertEqual(self._row.combo.itemText(1), "cohort  (folder)")

    # -- the download ---------------------------------------------------

    def test_picking_a_test_file_downloads_it_off_the_main_thread(self):
        """Not optional: this used to be a name that never travelled, and
        fetching it on the main thread froze the whole Slicer window."""
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"
        gate = threading.Event()
        self.client.gate = gate

        self._pick("MG_test_scan.nii.gz")

        # The pick has returned and the download has not finished: that is the
        # panel staying usable.
        self.assertEqual(len(_Job.started), 1)
        self.assertEqual(self._row.currentPath, "")
        gate.set()

        job = _Job.started[0]
        job.deliver()

        self.assertIsNot(job.worker_thread, threading.main_thread())
        self.assertIs(self.client.threads[0], job.worker_thread)
        self.assertEqual(self.client.calls, [("AREG", "MG_test_scan.nii.gz")])

    def test_the_download_lands_in_a_session_directory_not_in_documents(self):
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()

        path = self._row.currentPath
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(path.startswith(self.panel._testFileRoot))
        self.assertNotIn("Documents", path)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"scan!!")

    def test_it_becomes_an_ordinary_local_selection(self):
        """Once on disk it is uploaded like any other file: nothing travels as
        a bare name any more, and the dropdown goes back to its prompt."""
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()

        self.assertEqual(self._row.server_name(), "")
        self.assertEqual(ServerToolWidgetBase._serverSideSelections(self.panel), {})
        self.assertEqual(self._row.combo.currentIndex, 0)

    def test_a_second_pick_of_the_same_entry_downloads_nothing(self):
        """Cached by name for the session, so flipping between two cohorts is
        free after the first of each."""
        self._offer(
            {"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6},
            {"name": "other.nii.gz", "kind": "file", "size": 5},
        )
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"
        self.client.payloads["other.nii.gz"] = b"other"

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()
        first = self._row.currentPath

        self._pick("other.nii.gz")
        _Job.started[1].deliver()

        self._pick("MG_test_scan.nii.gz")

        # No second job at all, and the row is pointed at the same bytes.
        self.assertEqual(len(_Job.started), 2)
        self.assertEqual(self.client.calls.count(("AREG", "MG_test_scan.nii.gz")), 1)
        self.assertEqual(self._row.currentPath, first)

    def test_a_second_download_while_one_runs_is_refused_rather_than_raced(self):
        self._offer(
            {"name": "a.nii.gz", "kind": "file", "size": 1},
            {"name": "b.nii.gz", "kind": "file", "size": 1},
        )
        self.client.payloads = {"a.nii.gz": b"a", "b.nii.gz": b"b"}
        gate = threading.Event()
        self.client.gate = gate

        self._pick("a.nii.gz")
        self._pick("b.nii.gz")

        self.assertEqual(len(_Job.started), 1)
        gate.set()
        _Job.started[0].deliver()

    # -- what arrives ---------------------------------------------------

    def test_a_hosted_folder_is_unpacked_and_the_input_points_at_it(self):
        """The server has no way to put a directory on a wire, so it zips one;
        the tool takes a folder, so it is unpacked before the row sees it."""
        self._offer({"name": "CBCT_FullyAuto", "kind": "folder", "size": 512})
        self.client.payloads["CBCT_FullyAuto"] = {
            "patient1/scan.nii.gz": b"one",
            "patient2/scan.nii.gz": b"two",
        }

        self._pick("CBCT_FullyAuto")
        _Job.started[0].deliver()

        path = self._row.currentPath
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(self._row.is_folder())
        self.assertEqual(sorted(os.listdir(path)), ["patient1", "patient2"])
        # And the archive it arrived in is gone.
        self.assertFalse(os.path.exists(path + ".downloading"))

    def test_a_hosted_file_that_happens_to_be_a_zip_is_left_as_the_file(self):
        """It is what the server offered. Unpacking it would hand the tool
        something it never listed."""
        self._offer({"name": "cohort_10_patients.zip", "kind": "file", "size": 128})
        self.client.payloads["cohort_10_patients.zip"] = {"a.nii.gz": b"a"}

        self._pick("cohort_10_patients.zip")
        _Job.started[0].deliver()

        path = self._row.currentPath
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(zipfile.is_zipfile(path))

    def test_an_older_server_stating_no_kind_still_unpacks_a_folder(self):
        """No `entries` in the payload at all: a directory name carries no
        extension, and what arrived really is an archive."""
        data = {"models": [], "testfiles": ["CBCT_FullyAuto"]}
        self.panel._fillServerSelectable("t1", "testfile", data)
        self.client.payloads["CBCT_FullyAuto"] = {"patient1/scan.nii.gz": b"one"}

        self._pick("CBCT_FullyAuto")
        _Job.started[0].deliver()

        self.assertTrue(os.path.isdir(self._row.currentPath))

    def test_a_failed_download_leaves_no_half_finished_directory_behind(self):
        self._offer({"name": "CBCT_FullyAuto", "kind": "folder", "size": 1})
        self.client.error = ServerToolError("the server said no")

        self._pick("CBCT_FullyAuto")
        _Job.started[0].deliver()

        self.assertEqual(self._row.currentPath, "")
        # The owner mark is not a leftover download: it is how the sweep knows
        # this session is still alive and its directory is not to be removed.
        left = [name for name in os.listdir(self.panel._testFileRoot)
                if name != base_widget.ServerToolWidgetBase.OWNER_FILE]
        self.assertEqual(left, [])
        # And the panel is ready to try again.
        self.assertIsNone(self.panel._downloadJob)

    # -- what the scene is shown ----------------------------------------

    def test_a_single_file_is_loaded_into_the_scene(self):
        """The reason for downloading rather than naming: a clinician wants to
        look at the scan beside the panel."""
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()

        self.assertEqual(_util.loaded, [("volume", self._row.currentPath)])

    def test_a_mesh_is_loaded_as_a_model(self):
        self._offer({"name": "Upper_gold.vtk", "kind": "file", "size": 4})
        self.client.payloads["Upper_gold.vtk"] = b"mesh"

        self._pick("Upper_gold.vtk")
        _Job.started[0].deliver()

        self.assertEqual(_util.loaded, [("model", self._row.currentPath)])

    def test_a_folder_is_never_loaded_into_the_scene(self):
        """A forty-patient cohort would put hundreds of nodes in the scene,
        which is worse than showing nothing."""
        self._offer({"name": "CBCT_FullyAuto", "kind": "folder", "size": 512})
        self.client.payloads["CBCT_FullyAuto"] = {
            "patient1/scan.nii.gz": b"one",
            "patient2/scan.nii.gz": b"two",
        }

        self._pick("CBCT_FullyAuto")
        _Job.started[0].deliver()

        self.assertEqual(_util.loaded, [])
        self.assertTrue(os.path.isdir(self._row.currentPath))

    def test_a_file_the_scene_has_no_loader_for_is_not_an_error(self):
        self._offer({"name": "measurements.csv", "kind": "file", "size": 3})
        self.client.payloads["measurements.csv"] = b"a,b"

        self._pick("measurements.csv")
        _Job.started[0].deliver()

        self.assertEqual(_util.loaded, [])
        self.assertTrue(os.path.isfile(self._row.currentPath))

    def test_a_failed_scene_load_still_leaves_a_usable_input(self):
        """Loading is a courtesy. A reader Slicer refuses must not cost the
        user the file they just fetched - the run works either way."""
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        job = _Job.started[0]
        # The path is only known once the download lands, so the reader is
        # armed against whatever it produced.
        expected = os.path.join(self.panel._testFileDir(), "MG_test_scan.nii.gz")
        _util.load_failures = {expected}

        job.deliver()

        self.assertEqual(_util.loaded, [])
        self.assertEqual(self._row.currentPath, expected)
        self.assertTrue(os.path.isfile(expected))

    # -- progress -------------------------------------------------------

    def test_the_transfer_reports_progress_on_the_panel_channel(self):
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        job = _Job.started[0]
        job.deliver()

        self.assertTrue(any("MG_test_scan.nii.gz" in message for message in job.progress))


class SafeNameTest(unittest.TestCase):
    """The hosted name is the server's, and it is joined onto a local path."""

    def test_a_plain_name_is_kept(self):
        self.assertEqual(base_widget._safe_name("MG_test_scan.nii.gz"), "MG_test_scan.nii.gz")

    def test_separators_and_traversal_cannot_escape_the_download_directory(self):
        self.assertEqual(base_widget._safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(base_widget._safe_name("a/b.nii.gz"), "b.nii.gz")
        self.assertEqual(base_widget._safe_name(".."), "test_file")

class LoadingPhaseIsSaidOutLoudTest(HostedTestFileTest):
    """A user reported "the download takes more than 20 seconds". It does not:
    fetching a 94 MB scan over ranged parts is 0.3 s, measured against curl's
    0.26 s. The twenty seconds are Slicer decompressing the volume and building
    the image -- the feature that was asked for. The panel said "Downloading"
    throughout, so a fast transfer looked like a stalled one."""

    def test_the_scene_load_gets_its_own_progress_line(self):
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()

        scene = [line for line in self.panel.phases if "scene" in line.lower()]
        self.assertTrue(scene, self.panel.phases)
        self.assertIn("MG_test_scan.nii.gz", scene[-1])

    def test_the_line_is_painted_before_the_load_blocks(self):
        """`processEvents` between the message and the load, or the label is
        repainted only once the twenty seconds are already over."""
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"
        before = sys.modules["slicer"].app.processed

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()

        self.assertGreater(sys.modules["slicer"].app.processed, before)

    def test_a_folder_gets_no_scene_line_because_it_is_not_loaded(self):
        self._offer({"name": "cohort", "kind": "folder", "size": 40})
        self.client.payloads["cohort"] = {"a.nii.gz": b"x"}

        self._pick("cohort")
        _Job.started[0].deliver()

        self.assertFalse([line for line in self.panel.phases if "scene" in line.lower()],
                         self.panel.phases)


class LeftoverSweepTest(HostedTestFileTest):
    """Slicer's own docstring for `tempDirectory` says it: "This directory is
    not automatically cleaned up." The name carries a timestamp, so every
    session makes another one -- 648 MB per cohort, per launch, until the
    operating system gets round to /tmp, which on a workstation left running
    is never."""

    def _leftover(self, name, age_seconds):
        root = sys.modules["slicer"].app.temporaryPath
        path = os.path.join(root, name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "cohort.nii.gz"), "wb") as handle:
            handle.write(b"x" * 32)
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def _own(self, path, pid):
        with open(os.path.join(path, base_widget.ServerToolWidgetBase.OWNER_FILE), "w") as h:
            h.write(str(pid))

    def test_a_directory_whose_session_is_gone_is_removed(self):
        key = base_widget.ServerToolWidgetBase.TEST_FILE_DIR_KEY
        stale = self._leftover(key + "2026-09-01_10+00+00.000", 48 * 3600)
        # A pid that cannot be running: 0 is never a user process.
        self._own(stale, 2 ** 31 - 1)

        self.panel._testFileDir()

        self.assertFalse(os.path.exists(stale))

    def test_a_directory_a_live_session_owns_is_left_alone(self):
        """A SECOND Slicer may be running right now and own it, however long it
        has been open. An age threshold got this wrong in both directions: 2.4
        GB piled up in one afternoon at twelve hours, and a session open longer
        than the threshold could have had its own cohort deleted underneath
        it."""
        key = base_widget.ServerToolWidgetBase.TEST_FILE_DIR_KEY
        theirs = self._leftover(key + "2026-09-01_09+00+00.000", 72 * 3600)
        self._own(theirs, os.getpid())          # this very process is alive

        self.panel._testFileDir()

        self.assertTrue(os.path.exists(theirs))

    def test_a_directory_with_no_owner_mark_is_removed(self):
        """From a build before the mark existed. The worst case is a cohort
        someone re-downloads; the alternative is a disk that fills for good."""
        key = base_widget.ServerToolWidgetBase.TEST_FILE_DIR_KEY
        unmarked = self._leftover(key + "2026-08-01_09+00+00.000", 0)

        self.panel._testFileDir()

        self.assertFalse(os.path.exists(unmarked))

    def test_this_session_marks_its_own_directory(self):
        own = self.panel._testFileDir()

        marker = os.path.join(own, base_widget.ServerToolWidgetBase.OWNER_FILE)
        self.assertTrue(os.path.exists(marker))
        self.assertEqual(open(marker).read().strip(), str(os.getpid()))

    def test_another_modules_temp_directory_is_never_touched(self):
        """`tempDirectory()` is shared. Sweeping by key is what keeps this from
        deleting someone else's working files."""
        theirs = self._leftover("__SlicerTemp__2026-09-01_10+00+00.000", 48 * 3600)

        self.panel._testFileDir()

        self.assertTrue(os.path.exists(theirs))

    def test_a_sweep_that_cannot_run_does_not_cost_the_download(self):
        """Housekeeping must never fail a user's run."""
        app = sys.modules["slicer"].app
        original, app.temporaryPath = app.temporaryPath, "/does/not/exist"
        try:
            self.assertTrue(self.panel._testFileDir())
        finally:
            app.temporaryPath = original


class TimingsAreVisibleTest(HostedTestFileTest):
    """A user reported "the download takes more than ten seconds". It does not:
    inside Slicer a 94 MB scan is 1.7 s including the scene load, and a 7.4 MB
    cohort is 0.3 s. But `logger.info` does not reach Slicer's Python console,
    so the breakdown that would have settled it was measured and unreadable --
    the same mistake as the server's peak VRAM, recorded on every run and never
    read back."""

    def test_the_status_line_breaks_the_time_down_by_phase(self):
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()

        message = _util.status_messages[-1]
        self.assertIn("download", message)
        self.assertIn("scene", message)
        self.assertIn("MG_test_scan.nii.gz", message)

    def test_a_folder_reports_its_unpack_phase(self):
        self._offer({"name": "cohort", "kind": "folder", "size": 40})
        self.client.payloads["cohort"] = {"a.nii.gz": b"x"}

        self._pick("cohort")
        _Job.started[0].deliver()

        message = _util.status_messages[-1]
        self.assertIn("unpack", message)
        self.assertIn("download", message)

    def test_the_total_is_the_sum_of_the_phases(self):
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()

        # "ready in 0.3s (download 0.1s, move 0.0s, clean 0.0s, scene 0.2s)"
        message = _util.status_messages[-1]
        self.assertRegex(message, r"ready in \d+\.\d+s \(")


class TheBreakdownStaysOnThePanelTest(HostedTestFileTest):
    """`_hideProgress` used to run right after the load, so the one line saying
    where the seconds went lived only in the status bar, for eight seconds.
    That is no use to someone trying to find out why a download felt slow."""

    def test_the_summary_is_still_showing_when_the_pick_is_over(self):
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"

        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()

        self.assertIn("ready in", self.panel.phases[-1])
        self.assertIn("download", self.panel.phases[-1])

    def test_a_folder_reports_no_scene_phase_because_there_was_none(self):
        """Naming a phase that did not happen is worse than omitting it."""
        self._offer({"name": "cohort", "kind": "folder", "size": 40})
        self.client.payloads["cohort"] = {"a.nii.gz": b"x"}

        self._pick("cohort")
        _Job.started[0].deliver()

        summary = self.panel.phases[-1]
        self.assertIn("unpack", summary)
        self.assertNotIn("scene", summary)


class NothingIsLeftBehindTest(HostedTestFileTest):
    """Slicer removes nothing that `tempDirectory()` creates -- its own
    docstring says so -- and on this machine `/tmp` is on disk with no age
    limit in tmpfiles.d, so a directory from 28 August was still there on
    8 September. A clinician has no reason to know any of that exists."""

    def test_closing_the_panel_takes_this_session_s_downloads_with_it(self):
        self._offer({"name": "MG_test_scan.nii.gz", "kind": "file", "size": 6})
        self.client.payloads["MG_test_scan.nii.gz"] = b"scan!!"
        self._pick("MG_test_scan.nii.gz")
        _Job.started[0].deliver()
        root = self.panel._testFileRoot
        self.assertTrue(os.path.isdir(root))

        self.panel.cleanup()

        self.assertFalse(os.path.exists(root))

    def test_a_second_pick_after_cleanup_starts_a_fresh_directory(self):
        """Removing the cache must not leave the panel pointing at nothing."""
        self.panel._testFileDir()
        first = self.panel._testFileRoot
        self.panel.cleanup()

        second = self.panel._testFileDir()

        self.assertNotEqual(second, first)
        self.assertTrue(os.path.isdir(second))

    def test_opening_a_tool_sweeps_what_an_earlier_session_left(self):
        """On enter(), not only when someone picks a test file: a user who
        downloaded a cohort once and did not come back would keep it for good."""
        key = base_widget.ServerToolWidgetBase.TEST_FILE_DIR_KEY
        stale = os.path.join(sys.modules["slicer"].app.temporaryPath,
                             key + "2026-08-28_13+03+14.937")
        os.makedirs(stale, exist_ok=True)
        with open(os.path.join(stale, base_widget.ServerToolWidgetBase.OWNER_FILE), "w") as h:
            h.write(str(2 ** 31 - 1))

        # `enter()` also repaints and re-reads the server; the subject here is
        # only that the sweep is among the things it does.
        self.panel.uiWidget = None
        self.panel._refreshServerSelectables = lambda: None
        self.panel._refreshSceneVolumes = lambda: None
        self.panel._refreshServerStatus = lambda: None

        self.panel.enter()

        self.assertFalse(os.path.exists(stale))

    def test_cleanup_without_a_single_download_is_harmless(self):
        self.panel.cleanup()
        self.assertIsNone(self.panel._testFileRoot)


if __name__ == "__main__":
    unittest.main()
