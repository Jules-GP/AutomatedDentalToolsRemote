"""Several runs from one panel: the queue, the admission limit, and the cleanup.

A panel used to hold ONE `_job`, and Apply hid itself behind Cancel for the
duration. That is the wrong shape for the wait a clinician actually has, which
is a cohort: the upload of the next patient has no reason to sit behind the
inference of the previous one. `onApplyButton` now queues a `_Run` and
`_pumpRuns` starts as many as `config.CONCURRENT_RUNS` allows.

What these tests pin is the bookkeeping, not the threading -- `test_worker.py`
owns the thread. The failures they exist to catch are the quiet ones: a run
picking up another run's inputs, a scratch directory outliving its run, a
failure taking the whole queue down with it, and the callback closure binding
the wrong run.

The Slicer and Qt stand-ins come from `test_hosted_test_files`, which already
installs exactly the surface `base_widget` touches. Importing the module rather
than copying 120 lines of stubs keeps one definition of what Slicer looks like;
its own test classes stay in its namespace and are not re-run here.
"""

import os
import sys
import unittest

_HERE = os.path.abspath(os.path.dirname(__file__))
_CORE = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _HERE)
sys.path.insert(0, _CORE)

import test_hosted_test_files as fixtures  # noqa: F401,E402 - importing installs the stubs

# `_onJobSuccess` wraps `handleResult` in it, so it has to exist; the panel
# tests never reach that line, which is why the shared stub has no need of it.
_slicer_util = sys.modules["slicer.util"]
if not hasattr(_slicer_util, "tryWithErrorDisplay"):
    import contextlib

    @contextlib.contextmanager
    def _try_with_error_display(_message, **_kwargs):
        """Slicer swallows and reports; the tests want to see what escapes."""
        yield

    _slicer_util.tryWithErrorDisplay = _try_with_error_display

from ServerToolsCoreLib import base_widget, config  # noqa: E402
from ServerToolsCoreLib.base_widget import ServerToolWidgetBase  # noqa: E402

qt = sys.modules["qt"]


class _Job:
    """Stand-in for BackgroundJob: it records itself and delivers on command.

    No thread: what is under test is which run is started, when, and with what.
    A real worker thread would only make the order non-deterministic.
    """

    started = []

    def __init__(self, target, on_success=None, on_error=None, on_progress=None):
        self.target = target
        self.on_success = on_success
        self.on_error = on_error
        self.on_progress = on_progress
        self.cancelled = False

    def start(self):
        _Job.started.append(self)

    def cancel(self):
        self.cancelled = True

    def report(self, message):
        self.on_progress(message)

    def succeed(self, result="done"):
        self.on_success(result)

    def fail(self, error=None):
        self.on_error(error or RuntimeError("the server said no"))


class RunQueueTest(unittest.TestCase):
    def setUp(self):
        _Job.started = []
        self.addCleanup(setattr, base_widget, "BackgroundJob", base_widget.BackgroundJob)
        base_widget.BackgroundJob = _Job
        self.addCleanup(setattr, config, "CONCURRENT_RUNS", config.CONCURRENT_RUNS)
        config.CONCURRENT_RUNS = 1

        self.panel = self._panel()
        self.addCleanup(self.panel.onCancelButton)

    # -- the fixture ---------------------------------------------------

    def _panel(self):
        panel = ServerToolWidgetBase.__new__(ServerToolWidgetBase)
        panel.TOOL_NAME = "AREG"
        panel._runs = []
        panel._runsStarted = 0
        panel._elapsedTimer = None
        panel._outputFolderWidget = None
        panel._statusJob = None
        panel._downloadJob = None
        panel._testFileRoot = None
        panel.applyButton = qt.QPushButton("Apply")
        panel.cancelButton = qt.QPushButton("Cancel")
        panel.client = self

        # What the panel would read off its widgets. Held on the test so a case
        # can change it between two Apply clicks, which is how "the inputs are
        # read at Apply time" is checked at all.
        self.nextInput = "/data/patient_01.nii.gz"
        self.nextArgs = {"suffix": "_reg"}
        panel.prepareInputFiles = lambda workspace: {"t1": self.nextInput}
        panel.collectArgs = lambda: dict(self.nextArgs)

        # Everything downstream of a result, recorded rather than performed.
        self.handled = []
        self.phases = []
        panel.handleResult = self.handled.append
        panel._showPhase = self.phases.append
        panel._hideProgress = lambda: None
        panel._checkCanApply = lambda *args: None
        return panel

    def run_tool(self, *args, **kwargs):
        """The panel's `client.run`; never called, since the job is a stub."""
        raise AssertionError("the stub job never invokes its target")

    def _apply(self, path=None, **args):
        if path is not None:
            self.nextInput = path
        if args:
            self.nextArgs = args
        self.panel.onApplyButton()

    def _running(self):
        return [run for run in self.panel._runs if run.running]

    # -- queueing ------------------------------------------------------

    def test_a_second_apply_queues_instead_of_being_refused(self):
        self._apply()
        self._apply()

        self.assertEqual(len(self.panel._runs), 2)
        self.assertEqual(len(_Job.started), 1, "the limit is one at a time")
        self.assertIsNone(self.panel._runs[1].started_at, "the second is queued")

    def test_the_queued_run_starts_by_itself_when_the_first_ends(self):
        self._apply()
        self._apply()

        _Job.started[0].succeed()

        self.assertEqual(len(_Job.started), 2, "the queue must walk itself")
        self.assertEqual(len(self.panel._runs), 1)
        self.assertTrue(self.panel._runs[0].running)

    def test_the_limit_decides_how_many_move_together(self):
        config.CONCURRENT_RUNS = 2
        self._apply()
        self._apply()
        self._apply()

        self.assertEqual(len(_Job.started), 2)
        self.assertEqual(len(self._running()), 2)
        self.assertIsNone(self.panel._runs[2].started_at)

    def test_a_limit_below_one_is_still_one(self):
        """A misconfigured zero must not silently stop every panel."""
        config.CONCURRENT_RUNS = 0
        self._apply()
        self.assertEqual(len(_Job.started), 1)

    def test_a_nonsense_limit_falls_back_to_a_queue(self):
        config.CONCURRENT_RUNS = "beaucoup"
        self._apply()
        self._apply()
        self.assertEqual(len(_Job.started), 1)

    # -- what a run carries --------------------------------------------

    def test_a_queued_run_keeps_the_inputs_it_was_given(self):
        """Read at Apply time, not at start time.

        A run that starts three minutes later because two were ahead of it must
        not pick up whatever the pickers hold by then -- that would silently run
        patient 3's scan under patient 1's request.
        """
        self._apply(path="/data/patient_01.nii.gz", suffix="_one")
        self._apply(path="/data/patient_02.nii.gz", suffix="_two")

        second = self.panel._runs[1]
        self.assertEqual(second.files, {"t1": "/data/patient_02.nii.gz"})
        self.assertEqual(second.args, {"suffix": "_two"})

        self.nextInput = "/data/patient_99.nii.gz"
        _Job.started[0].succeed()

        self.assertEqual(second.files, {"t1": "/data/patient_02.nii.gz"})

    def test_each_run_gets_its_own_scratch_directory(self):
        config.CONCURRENT_RUNS = 2
        self._apply()
        self._apply()

        first, second = self.panel._runs
        self.assertNotEqual(first.workspace.path, second.workspace.path)
        self.assertTrue(os.path.isdir(first.workspace.path))
        self.assertTrue(os.path.isdir(second.workspace.path))

    def test_finishing_one_run_removes_only_its_own_directory(self):
        config.CONCURRENT_RUNS = 2
        self._apply()
        self._apply()
        first, second = self.panel._runs
        gone, kept = first.workspace.path, second.workspace.path

        _Job.started[0].succeed()

        self.assertFalse(os.path.exists(gone))
        self.assertTrue(os.path.isdir(kept), "a sibling's inputs were deleted")

    def test_the_output_folder_defaults_to_the_run_s_own_directory(self):
        self._apply()
        run = self.panel._runs[0]
        self.assertEqual(run.output_dir, run.workspace.path)

    def test_inputs_that_cannot_be_prepared_queue_nothing(self):
        def explode(_workspace):
            raise ValueError("no scan selected")

        self.panel.prepareInputFiles = explode
        self._apply()

        self.assertEqual(self.panel._runs, [])
        self.assertEqual(_Job.started, [])

    # -- callbacks land on the right run -------------------------------

    def test_each_callback_reports_against_its_own_run(self):
        """The closure trap: without `run=run`, every callback would report
        against whichever run was queued last."""
        config.CONCURRENT_RUNS = 2
        self._apply(path="/data/one.nii.gz")
        self._apply(path="/data/two.nii.gz")

        _Job.started[0].report("uploading one")
        _Job.started[1].report("uploading two")

        self.assertEqual(self.panel._runs[0].phase, "uploading one")
        self.assertEqual(self.panel._runs[1].phase, "uploading two")

    def test_a_result_is_handed_over_once_per_run(self):
        config.CONCURRENT_RUNS = 2
        self._apply()
        self._apply()

        _Job.started[1].succeed("second")
        _Job.started[0].succeed("first")

        self.assertEqual(self.handled, ["second", "first"])

    def test_one_run_failing_leaves_the_rest_alone(self):
        config.CONCURRENT_RUNS = 2
        self._apply()
        self._apply()
        self._apply()

        _Job.started[0].fail()

        self.assertEqual(len(self.panel._runs), 2)
        self.assertEqual(len(_Job.started), 3, "the queue advanced past the failure")

    # -- cancelling and closing ----------------------------------------

    def test_cancel_takes_the_queue_with_it(self):
        self._apply()
        self._apply()
        directories = [run.workspace.path for run in self.panel._runs]

        self.panel.onCancelButton()

        self.assertEqual(self.panel._runs, [])
        self.assertTrue(_Job.started[0].cancelled)
        for path in directories:
            self.assertFalse(os.path.exists(path))

    def test_cleanup_cancels_every_run(self):
        config.CONCURRENT_RUNS = 2
        self._apply()
        self._apply()
        directories = [run.workspace.path for run in self.panel._runs]
        self.panel._removeOwnTestFiles = lambda: None

        self.panel.cleanup()

        self.assertEqual(self.panel._runs, [])
        self.assertTrue(all(job.cancelled for job in _Job.started))
        for path in directories:
            self.assertFalse(os.path.exists(path))

    # -- what the panel shows ------------------------------------------

    def test_apply_stays_available_so_another_run_can_be_queued(self):
        self._apply()
        self.assertTrue(self.panel.applyButton.isVisible())
        self.assertTrue(self.panel.cancelButton.isVisible())

    def test_the_cancel_button_says_how_much_it_cancels(self):
        self._apply()
        self.assertEqual(self.panel.cancelButton.text, "Cancel")
        self._apply()
        self.assertEqual(self.panel.cancelButton.text, "Cancel all")

    def test_cancel_is_hidden_again_once_nothing_is_left(self):
        self._apply()
        _Job.started[0].succeed()
        self.assertFalse(self.panel.cancelButton.isVisible())

    def test_one_run_reads_exactly_as_it_always_did(self):
        """Nine modules' worth of habit: a single run must gain no prefix."""
        self._apply()
        _Job.started[0].report("Uploading...")

        line = self.phases[-1]
        self.assertIn("Uploading...", line)
        self.assertIn("elapsed", line)
        self.assertNotIn("Run 1", line)

    def test_several_runs_get_one_line_each_naming_what_they_are(self):
        config.CONCURRENT_RUNS = 2
        self._apply(path="/data/patient_01.nii.gz")
        self._apply(path="/data/patient_02.nii.gz")

        lines = self.phases[-1].splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("patient_01.nii.gz", lines[0])
        self.assertIn("patient_02.nii.gz", lines[1])

    def test_a_queued_run_says_it_is_queued(self):
        self._apply(path="/data/patient_01.nii.gz")
        self._apply(path="/data/patient_02.nii.gz")

        lines = self.phases[-1].splitlines()
        self.assertIn("queued", lines[1])
        self.assertNotIn("queued", lines[0])

    def test_run_numbers_keep_counting_across_a_finished_run(self):
        """Two runs must never both be called "Run 1"."""
        self._apply()
        _Job.started[0].succeed()
        self._apply()
        self.assertEqual(self.panel._runs[0].number, 2)


class OneJobPerToolTest(unittest.TestCase):
    """The default shape: one run at a time per tool, several tools at once.

    This is what a clinician expects of a panel, and what the panel did before
    it learned to queue -- minus the part where a second Apply was simply
    refused. Two properties make it up, and they are independent:

    - within ONE tool, a second Apply queues and starts by itself;
    - across tools, nothing is shared, so each panel holds its own run and they
      overlap. The cap that matters there is the server's MAX_CONCURRENT_TOOLS,
      not anything here.
    """

    def setUp(self):
        _Job.started = []
        self.addCleanup(setattr, base_widget, "BackgroundJob", base_widget.BackgroundJob)
        base_widget.BackgroundJob = _Job
        self.addCleanup(setattr, config, "CONCURRENT_RUNS", config.CONCURRENT_RUNS)

    def _panel(self, tool_name):
        panel = ServerToolWidgetBase.__new__(ServerToolWidgetBase)
        panel.TOOL_NAME = tool_name
        panel._runs = []
        panel._runsStarted = 0
        panel._elapsedTimer = None
        panel._outputFolderWidget = None
        panel.applyButton = qt.QPushButton("Apply")
        panel.cancelButton = qt.QPushButton("Cancel")
        panel.prepareInputFiles = lambda workspace: {"t1": "/data/" + tool_name + ".nii.gz"}
        panel.collectArgs = lambda: {}
        panel.handleResult = lambda result: None
        panel._showPhase = lambda text: None
        panel._hideProgress = lambda: None
        panel._checkCanApply = lambda *args: None
        self.addCleanup(panel.onCancelButton)
        return panel

    def test_the_shipped_default_is_one_run_at_a_time(self):
        """A panel running two of the same tool at once is not what a panel
        looks like. The overlap is opt-in, not the resting state."""
        self.assertEqual(config.CONCURRENT_RUNS, 1)

    def test_a_second_apply_on_one_tool_waits_for_the_first(self):
        config.CONCURRENT_RUNS = 1
        panel = self._panel("AMASSS")

        panel.onApplyButton()
        panel.onApplyButton()

        self.assertEqual(len(_Job.started), 1)
        self.assertEqual(len(panel._runs), 2, "the second is kept, not refused")

    def test_two_tools_run_at_the_same_time(self):
        config.CONCURRENT_RUNS = 1
        amasss = self._panel("AMASSS")
        ali = self._panel("ALI")

        amasss.onApplyButton()
        ali.onApplyButton()

        self.assertEqual(len(_Job.started), 2, "one panel held the other back")
        self.assertTrue(amasss._runs[0].running)
        self.assertTrue(ali._runs[0].running)

    def test_one_tool_s_queue_is_not_the_other_s(self):
        config.CONCURRENT_RUNS = 1
        amasss = self._panel("AMASSS")
        ali = self._panel("ALI")

        amasss.onApplyButton()
        amasss.onApplyButton()
        ali.onApplyButton()

        self.assertEqual(len(amasss._runs), 2)
        self.assertEqual(len(ali._runs), 1)
        self.assertEqual(len(_Job.started), 2, "ALI waited behind AMASSS's queue")

    def test_cancelling_one_tool_leaves_the_other_running(self):
        config.CONCURRENT_RUNS = 1
        amasss = self._panel("AMASSS")
        ali = self._panel("ALI")
        amasss.onApplyButton()
        ali.onApplyButton()

        amasss.onCancelButton()

        self.assertEqual(amasss._runs, [])
        self.assertEqual(len(ali._runs), 1)
        self.assertTrue(ali._runs[0].running)

    def test_raising_the_limit_is_all_it_takes_to_overlap(self):
        """The whole difference between the two shapes, in one number."""
        config.CONCURRENT_RUNS = 2
        panel = self._panel("AMASSS")

        panel.onApplyButton()
        panel.onApplyButton()

        self.assertEqual(len(_Job.started), 2)


class RunLabelTest(unittest.TestCase):
    """A run is named after what it was given, so a cohort's lines read."""

    def setUp(self):
        self.panel = ServerToolWidgetBase.__new__(ServerToolWidgetBase)
        self.panel.TOOL_NAME = "AREG"

    def test_it_is_the_input_s_file_name(self):
        self.assertEqual(
            self.panel._runLabel({"t1": "/data/cohort/patient_07.nii.gz"}),
            "patient_07.nii.gz")

    def test_a_folder_keeps_its_own_name_not_its_parent_s(self):
        self.assertEqual(self.panel._runLabel({"t1": "/data/cohort/T1/"}), "T1")

    def test_with_nothing_uploaded_it_falls_back_to_the_tool(self):
        self.assertEqual(self.panel._runLabel({}), "AREG")

    def test_an_empty_path_is_not_a_name(self):
        self.assertEqual(self.panel._runLabel({"t1": ""}), "AREG")


if __name__ == "__main__":
    unittest.main()
