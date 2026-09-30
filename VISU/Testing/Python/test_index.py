"""The index, against the folder shapes the tools actually produce.

Every tree here is taken from a real tool's naming code rather than invented:
ALI writes landmarks and no scan, ASO writes an oriented scan BESIDE its
landmarks, AMASSS puts the scan's stem in a folder name, Crown_Seg files half
a batch one level deeper than the other half.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from VISULib import index  # noqa: E402


def tree(root, paths):
    for relative in paths:
        full = os.path.join(root, relative)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write("x")


class PatientStemTest(unittest.TestCase):
    def test_every_tool_s_name_for_one_patient_answers_the_same(self):
        for filename in (
            "P1_scan.nii.gz",            # the acquisition
            "P1_scan_lm_Pred.mrk.json",  # ALI
            "P1_Or.nii.gz",              # ASO's oriented scan
            "P1_lm_Or.mrk.json",         # ASO's landmarks
            "P1_Seg.vtk",                # Crown_Seg
            "P1_MERGED.nii.gz",          # AMASSS merged
        ):
            self.assertEqual(index.patient_stem(filename), "P1", filename)

    def test_a_suffix_inside_a_token_does_not_truncate(self):
        # `P_Seg1` and `P_Seg2` are two subjects. Matching `_Seg` anywhere
        # collapsed them onto one upstream, and lost one of them.
        self.assertEqual(index.patient_stem("P_Seg1_T1.nii.gz"), "P_Seg1_T1")
        self.assertEqual(index.patient_stem("P_Seg2_T1.nii.gz"), "P_Seg2_T1")

    def test_timepoints_are_separate_scans_unless_asked(self):
        self.assertEqual(index.patient_stem("P1_T1_scan.nii.gz"), "P1_T1")
        self.assertEqual(
            index.patient_stem("P1_T1_scan.nii.gz", drop_timepoint=True), "P1"
        )

    def test_a_name_that_is_only_a_suffix_keeps_it(self):
        self.assertEqual(index.patient_stem("_Or.nii.gz"), "_Or")


class KindTest(unittest.TestCase):
    def test_a_mask_token_makes_a_scan_labelled_voxels(self):
        self.assertEqual(index.kind_of("P1_scan.nii.gz"), index.VOLUME)
        self.assertEqual(index.kind_of("P1_Pred_MAND.nii.gz"), index.LABELMAP)
        self.assertEqual(index.kind_of("P1_seg.nii.gz"), index.LABELMAP)

    def test_a_patient_named_after_a_token_is_not_a_mask(self):
        # Whole tokens: `"seg" in name` would make SEGOVIA_01 a segmentation.
        self.assertEqual(index.kind_of("SEGOVIA_01.nii.gz"), index.VOLUME)

    def test_the_other_kinds(self):
        self.assertEqual(index.kind_of("P1_lm_Pred.mrk.json"), index.MARKUPS)
        self.assertEqual(index.kind_of("arch.vtk"), index.MODEL)
        self.assertEqual(index.kind_of("P1_Or_transform.tfm"), index.TRANSFORM)
        self.assertIsNone(index.kind_of("notes.txt"))


class BuildTest(unittest.TestCase):
    def test_ali_landmarks_and_the_scan_they_belong_to_are_one_case(self):
        with tempfile.TemporaryDirectory() as root:
            tree(root, ["scans/P1_scan.nii.gz", "out/P1_scan_lm_Pred.mrk.json"])
            cases = index.build(
                [("Scans", os.path.join(root, "scans")),
                 ("Results", os.path.join(root, "out"))]
            )
            self.assertEqual([case.key for case in cases], ["P1"])
            self.assertEqual(len(cases[0].artifacts), 2)

    def test_reports_and_killed_run_scratch_are_not_patients(self):
        with tempfile.TemporaryDirectory() as root:
            tree(root, [
                "out/P1_scan_lm_Pred.mrk.json",
                "out/run_report.json",
                "out/AMASSS_report.json",
                "out/.amasss_work/p_000_0000.nii.gz",
            ])
            cases = index.build([("Results", os.path.join(root, "out"))])
            self.assertEqual([case.key for case in cases], ["P1"])

    def test_two_patients_of_the_same_name_in_different_folders_stay_apart(self):
        with tempfile.TemporaryDirectory() as root:
            tree(root, ["out/siteA/p01_scan.nii.gz", "out/siteB/p01_scan.nii.gz"])
            cases = index.build([("Results", os.path.join(root, "out"))])
            self.assertEqual(
                [case.key for case in cases],
                [os.path.join("siteA", "p01"), os.path.join("siteB", "p01")],
            )

    def test_amasss_takes_the_patient_from_the_folder_it_invented(self):
        # `<stem>_<prediction_ID>_SegOut/` is the only place the scan's stem
        # survives: the files inside carry a free-text id in the middle.
        with tempfile.TemporaryDirectory() as root:
            tree(root, [
                "scans/MG_test_scan.nii.gz",
                "out/MG_test_scan_Pred_SegOut/MG_test_scan_Pred_MAND.nii.gz",
                "out/MG_test_scan_Pred_SegOut/MG_test_scan_Pred_CB.nii.gz",
            ])
            cases = index.build(
                [("Scans", os.path.join(root, "scans")),
                 ("Results", os.path.join(root, "out"))]
            )
            self.assertEqual([case.key for case in cases], ["MG_test"])
            self.assertEqual(len(cases[0].artifacts), 3)

    def test_crown_seg_s_two_branches_land_in_one_case_each(self):
        with tempfile.TemporaryDirectory() as root:
            tree(root, [
                "out/siteA/passed_Seg.vtk",                      # already labelled
                "out/crownseg_input_Seg/siteA/fresh_Seg.vtk",     # segmented here
            ])
            cases = index.build([("Results", os.path.join(root, "out"))])
            self.assertEqual(
                [case.key for case in cases],
                [os.path.join("siteA", "fresh"), os.path.join("siteA", "passed")],
            )


class RealShapesTest(unittest.TestCase):
    """The three shapes the hosted test data has and a suffix table misses."""

    def test_a_dicom_series_is_one_volume_named_after_its_folder(self):
        # 577 slices called IMG0375.dcm are one scan, and the folder is the
        # only place its patient name appears.
        with tempfile.TemporaryDirectory() as root:
            tree(root, [
                "scans/CBCT_DCM/IC_0005/IMG0001.dcm",
                "scans/CBCT_DCM/IC_0005/IMG0002.dcm",
                "scans/CBCT_DCM/IC_0005_lm_Pred.mrk.json",
            ])
            cases = index.build([("Scans", os.path.join(root, "scans"))])
            self.assertEqual([case.key for case in cases],
                             [os.path.join("CBCT_DCM", "IC_0005")])
            kinds = sorted(artifact.kind for artifact in cases[0].artifacts)
            self.assertEqual(kinds, [index.MARKUPS, index.VOLUME])
            series = cases[0].of_kind(index.VOLUME)[0]
            self.assertTrue(os.path.isdir(series.path))

    def test_a_bare_json_counts_as_markups_only_when_it_is_some(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "out"))
            landmarks = os.path.join(root, "out", "P1_Upper_O_Pred.json")
            with open(landmarks, "w", encoding="utf-8") as handle:
                handle.write('{"@schema": "x", "markups": [{"type": "Fiducial"}]}')
            with open(os.path.join(root, "out", "settings.json"), "w",
                      encoding="utf-8") as handle:
                handle.write('{"device": "cuda"}')
            cases = index.build([("Results", os.path.join(root, "out"))])
            self.assertEqual([case.key for case in cases], ["P1_Upper_O_Pred"])
            self.assertEqual(cases[0].artifacts[0].kind, index.MARKUPS)

    def test_aso_ios_landmarks_reach_the_surface_they_do_not_share_a_stem_with(self):
        # `Upper_new_9.vtk` and `Upper_new_9_Upper_O_Pred.json`, the hosted
        # IOS_SemiAuto pair. No suffix table reaches it; a token-aligned
        # prefix does.
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "ios"))
            with open(os.path.join(root, "ios", "Upper_new_9.vtk"), "w") as handle:
                handle.write("x")
            with open(os.path.join(root, "ios", "Upper_new_9_Upper_O_Pred.json"),
                      "w", encoding="utf-8") as handle:
                handle.write('{"markups": []}')
            cases = index.build([("Scans", os.path.join(root, "ios"))])
            self.assertEqual([case.key for case in cases], ["Upper_new_9"])
            self.assertEqual(len(cases[0].artifacts), 2)

    def test_a_cohort_filed_by_role_is_one_case_per_patient(self):
        # `<root>/CBCT/` beside `<root>/Landmarks/` is how a reader files a
        # cohort. It is one patient, not two.
        with tempfile.TemporaryDirectory() as root:
            tree(root, [
                "data/CBCT/p1_scan.nii.gz",
                "data/CBCT/p2_scan.nii.gz",
                "data/Landmarks/p1_scan_lm_Pred.mrk.json",
                "data/Landmarks/p2_scan_lm_Pred.mrk.json",
            ])
            cases = index.build([("folder", os.path.join(root, "data"))])
            self.assertEqual([case.key for case in cases], ["p1", "p2"])
            self.assertEqual(len(cases[0].artifacts), 2)

    def test_role_folders_do_not_make_two_files_look_co_located(self):
        # Stripping `CBCT/` and `Landmarks/` from the KEY must not also claim
        # the points were written beside the scan. They were not, and whether
        # they share its frame is unknown.
        with tempfile.TemporaryDirectory() as root:
            tree(root, ["d/CBCT/p1_scan.nii.gz", "d/Landmarks/p1_scan_lm_Pred.mrk.json"])
            case = index.build([("folder", os.path.join(root, "d"))])[0]
            views = [v for v in case.views(acquisition="folder") if v.overlays]
            self.assertEqual(len(views), 1)
            self.assertEqual(views[0].anchor.name, "p1_scan.nii.gz")
            self.assertEqual(views[0].basis, index.BASIS_ACQUISITION)

    def test_a_role_folder_of_several_words_is_still_one(self):
        # The hosted AREG fixture files one subject under `CBCT Landmarks/`,
        # `IOS Landmarks/` and `T2/`. Matching exact names left it indexed
        # three times.
        with tempfile.TemporaryDirectory() as root:
            tree(root, [
                "d/T2/P_0001_T2.nii.gz",
                "d/CBCT Landmarks/P_0001_T2_lm_Pred_U.mrk.json",
                "d/IOS Landmarks/P_0001_T2_lm_Pred_L.mrk.json",
            ])
            cases = index.build([("folder", os.path.join(root, "d"))])
            self.assertEqual([case.key for case in cases], ["P_0001_T2"])
            self.assertEqual(len(cases[0].artifacts), 3)

    def test_a_folder_named_after_a_subject_is_never_a_role(self):
        # Every token has to be a role word, or `patient_T1/` would vanish.
        self.assertTrue(index.is_role_level("Landmarks"))
        self.assertTrue(index.is_role_level("CBCT Landmarks"))
        self.assertTrue(index.is_role_level("T2"))
        self.assertFalse(index.is_role_level("patient_T1"))
        self.assertFalse(index.is_role_level("P_0001_T2"))
        self.assertFalse(index.is_role_level("cohort_6"))

    def test_ios_is_a_role_folder_too(self):
        with tempfile.TemporaryDirectory() as root:
            tree(root, ["d/IOS/Upper_new_9.vtk", "d/Landmarks/Upper_new_9_Upper_O.mrk.json"])
            cases = index.build([("folder", os.path.join(root, "d"))])
            self.assertEqual([case.key for case in cases], ["Upper_new_9"])

    def test_loose_masks_join_the_scan_they_were_made_from(self):
        # AMASSS names a mask `<stem>_<prediction_ID>_<CODE>`, and the id is
        # free text no suffix table can strip. Loose in a folder -- no
        # `_SegOut/` level to read it off -- each one used to index as its own
        # patient.
        with tempfile.TemporaryDirectory() as root:
            tree(root, [
                "d/CBCT/p1_scan.nii.gz",
                "d/Masks/p1_scan_Pred_MAND.nii.gz",
                "d/Masks/p1_scan_Pred_MAX.nii.gz",
                "d/Landmarks/p1_scan_lm_Pred.mrk.json",
            ])
            cases = index.build([("folder", os.path.join(root, "d"))])
            self.assertEqual([case.key for case in cases], ["p1"])
            kinds = sorted(a.kind for a in cases[0].artifacts)
            self.assertEqual(kinds, [index.LABELMAP, index.LABELMAP,
                                     index.MARKUPS, index.VOLUME])

    def test_a_mask_with_no_scan_anywhere_is_still_a_case(self):
        # Absorbed only when there is something to absorb it INTO.
        with tempfile.TemporaryDirectory() as root:
            tree(root, ["d/p1_scan_Pred_MAND.nii.gz"])
            cases = index.build([("folder", os.path.join(root, "d"))])
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].artifacts[0].kind, index.LABELMAP)

    def test_a_prefix_that_is_another_patient_does_not_absorb_it(self):
        # `P1` and `P10` are two patients. Matching on substring paired them
        # upstream and padded the list with a sentinel to hide it.
        self.assertTrue(index.is_token_prefix("P1", "P1_lm_Pred"))
        self.assertFalse(index.is_token_prefix("P1", "P10_lm_Pred"))
        self.assertFalse(index.is_token_prefix("P1", "P1"))


class LabelTest(unittest.TestCase):
    def test_a_case_reads_as_the_patient_with_the_folder_behind_it(self):
        case = index.Case(os.path.join("CBCT_SemiAuto", "IC_0005"))
        self.assertEqual(case.patient, "IC_0005")
        self.assertEqual(case.label, "IC_0005  (CBCT_SemiAuto)")

    def test_a_case_at_the_root_is_just_its_name(self):
        self.assertEqual(index.Case("IC_0005").label, "IC_0005")


class ViewTest(unittest.TestCase):
    """Which scan an overlay is drawn on. The one thing that must not lie."""

    def _case(self, root, paths, sources):
        tree(root, paths)
        cases = index.build([(label, os.path.join(root, path)) for label, path in sources])
        self.assertEqual(len(cases), 1, [case.key for case in cases])
        return cases[0]

    def test_aso_landmarks_bind_to_the_oriented_scan_not_the_original(self):
        # ASO's points carry the recentring AND the ICP rotation, so they match
        # the scan ASO WROTE. Drawn on the acquisition they render fine and are
        # wrong by a rotation.
        with tempfile.TemporaryDirectory() as root:
            case = self._case(
                root,
                ["scans/P1_scan.nii.gz",
                 "out/P1_Or.nii.gz",
                 "out/P1_lm_Or.mrk.json",
                 "out/P1_Or_transform.tfm"],
                [("Scans", "scans"), ("Results", "out")],
            )
            views = case.views(acquisition="Scans")
            with_points = [view for view in views if view.overlays]
            self.assertEqual(len(with_points), 1)
            self.assertEqual(with_points[0].anchor.name, "P1_Or.nii.gz")
            self.assertEqual(with_points[0].basis, index.BASIS_COLOCATED)
            # And the acquisition is still offered, carrying nothing.
            self.assertIn("P1_scan.nii.gz", [view.label for view in views])

    def test_ali_landmarks_fall_back_to_the_acquisition_and_say_so(self):
        # ALI writes no scan at all, so its points can only be drawn on the
        # input -- where they are right, measured at 0.000 mm against ITK.
        with tempfile.TemporaryDirectory() as root:
            case = self._case(
                root,
                ["scans/P1_scan.nii.gz", "out/P1_scan_lm_Pred.mrk.json"],
                [("Scans", "scans"), ("Results", "out")],
            )
            views = case.views(acquisition="Scans")
            with_points = [view for view in views if view.overlays]
            self.assertEqual(len(with_points), 1)
            self.assertEqual(with_points[0].anchor.name, "P1_scan.nii.gz")
            self.assertEqual(with_points[0].basis, index.BASIS_ACQUISITION)

    def test_landmarks_with_no_scan_anywhere_are_offered_without_one(self):
        with tempfile.TemporaryDirectory() as root:
            case = self._case(
                root, ["out/P1_scan_lm_Pred.mrk.json"], [("Results", "out")]
            )
            views = case.views(acquisition="Scans")
            self.assertEqual(len(views), 1)
            self.assertIsNone(views[0].anchor)
            self.assertEqual(views[0].basis, index.BASIS_NONE)


if __name__ == "__main__":
    unittest.main()


class FolderTest(unittest.TestCase):
    """Which level of a folder a case came from."""

    def _cases(self, paths):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        tree(root.name, paths)
        return index.build([("folder", os.path.join(root.name, "d"))])

    def test_a_case_at_the_top_is_named_rather_than_left_blank(self):
        cases = self._cases(["d/p1_scan.nii.gz"])
        self.assertEqual(index.folder_of(cases[0]), index.AT_THE_TOP)

    def test_the_first_level_is_what_a_reader_picks_between(self):
        # Deeper levels are how a tool mirrors an input tree; one chip per
        # patient would be a second case list.
        cases = self._cases(["d/CBCT_SemiAuto/siteA/p1_scan.nii.gz"])
        self.assertEqual(index.folder_of(cases[0]), "CBCT_SemiAuto")

    def test_they_are_listed_in_the_order_the_cases_are(self):
        cases = self._cases(["d/IOS_SemiAuto/u1.vtk",
                             "d/CBCT_SemiAuto/p1_scan.nii.gz",
                             "d/CBCT_SemiAuto/p2_scan.nii.gz"])
        self.assertEqual(index.folders_in(cases), ["CBCT_SemiAuto", "IOS_SemiAuto"])

    def test_a_role_folder_is_never_offered_as_one(self):
        """`CBCT/` beside `Landmarks/` is how ONE cohort is filed, not two to
        pick between -- and stripping them is what made the scan and its
        points one patient in the first place."""
        cases = self._cases(["d/CBCT/p1_scan.nii.gz",
                             "d/Landmarks/p1_scan_lm_Pred.mrk.json"])
        self.assertEqual(len(cases), 1)
        self.assertEqual(index.folders_in(cases), [index.AT_THE_TOP])

    def test_a_folder_with_no_levels_offers_one_entry(self):
        cases = self._cases(["d/p1_scan.nii.gz", "d/p2_scan.nii.gz"])
        self.assertEqual(index.folders_in(cases), [index.AT_THE_TOP])


class RegisteredComparisonTest(unittest.TestCase):
    """Two scans in ONE picture, which every other basis exists to refuse.

    `View`'s own docstring says an oriented scan and the one it was made from
    are not the same picture, and that drawing either on the other is wrong by
    a rotation and renders without an error. A registration is the exception
    and is honest by construction -- the result was resampled into the
    target's frame -- so it is DECLARED by the tool, never detected here.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _folders(self, sent, produced, manifest=True):
        """Two labelled folders, and the run's own word on what belongs where.

        The manifest is what makes them ONE case. Without it the input and the
        output key apart -- `P1_T1` against `P1_Reg` -- and no table of markers
        can fix that, because AREG's output suffix is an argument the caller
        chooses.
        """
        for label, names in (("sent", sent), ("produced", produced)):
            folder = os.path.join(self.root, label)
            os.makedirs(folder, exist_ok=True)
            for name in names:
                open(os.path.join(folder, name), "w").close()
        if manifest:
            cases = {}
            for index_of, name in enumerate(produced):
                cases.setdefault("P1", {"produced": [], "inputs": {}})
                cases["P1"]["produced"].append(name)
            for name in sent:
                cases.setdefault("P1", {"produced": [], "inputs": {}})
                cases["P1"]["inputs"]["t1"] = name
            with open(os.path.join(self.root, "produced", "Tool_report.json"),
                      "w", encoding="utf-8") as handle:
                json.dump({"cases": cases}, handle)
        return [("Scans", os.path.join(self.root, "sent")),
                ("Results", os.path.join(self.root, "produced"))]

    def test_the_acquisition_anchors_and_the_result_is_compared_to_it(self):
        cases = index.build(self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz"]))

        views = cases[0].views(acquisition="Scans", registered=True)

        self.assertEqual(len(views), 1, "one picture, not two")
        self.assertEqual(views[0].anchor.name, "P1_T1.nii.gz")
        self.assertEqual(views[0].compare.name, "P1_Reg.nii.gz")
        self.assertEqual(views[0].basis, index.BASIS_REGISTERED)

    def test_without_the_declaration_they_stay_two_pictures(self):
        """The default, and what every tool that is not a registration gets.

        One case -- the manifest said so -- and still two pictures, because
        nobody said the two scans share a frame.
        """
        cases = index.build(self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz"]))

        views = cases[0].views(acquisition="Scans")

        self.assertEqual(len(views), 2)
        self.assertTrue(all(view.compare is None for view in views))

    def test_with_no_manifest_the_two_files_are_two_cases(self):
        """What it was before, and the reason the manifest exists: an input
        and an output of one patient key apart, so nothing holds both."""
        cases = index.build(
            self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz"], manifest=False))

        self.assertEqual(sorted(case.key for case in cases), ["P1_Reg", "P1_T1"])

    def test_a_case_the_run_produced_nothing_for_is_not_shown_an_empty_compare(self):
        cases = index.build(self._folders(["P1_T1.nii.gz"], []))

        views = cases[0].views(acquisition="Scans", registered=True)

        self.assertEqual([view.compare for view in views], [None])

    def test_a_result_with_no_acquisition_beside_it_falls_through(self):
        """A reader who opened the results folder alone. There is nothing to
        compare against, and inventing an anchor would be the rotation bug."""
        cases = index.build(self._folders([], ["P1_Reg.nii.gz"]))

        views = cases[0].views(acquisition="Scans", registered=True)

        self.assertEqual([view.compare for view in views], [None])
        self.assertEqual(views[0].anchor.name, "P1_Reg.nii.gz")

    def test_the_declared_anchor_wins_over_the_folder_it_came_from(self):
        """AREG's oriented mode registers against a copy IT made.

        That copy arrives in the RESULTS, not in the acquisition, so picking
        the anchor by source would land on the scan the caller sent -- wrong
        by exactly the rotation ASO applied, and rendering without an error.
        """
        folder = os.path.join(self.root, "produced")
        os.makedirs(folder, exist_ok=True)
        for name in ("P1_T1_Or.nii.gz", "P1_Reg.nii.gz"):
            open(os.path.join(folder, name), "w").close()
        os.makedirs(os.path.join(self.root, "sent"), exist_ok=True)
        open(os.path.join(self.root, "sent", "P1_T1.nii.gz"), "w").close()
        with open(os.path.join(folder, "Tool_report.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"cases": {"P1": {
                "produced": ["P1_Reg.nii.gz", "P1_T1_Or.nii.gz"],
                "inputs": {"t1": "P1_T1.nii.gz"},
                "registered_onto": "P1_T1_Or.nii.gz"}}}, handle)

        cases = index.build([("Scans", os.path.join(self.root, "sent")),
                             ("Results", folder)])
        views = cases[0].views(acquisition="Scans", registered=True)

        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].anchor.name, "P1_T1_Or.nii.gz",
                         "the oriented copy, not the scan the caller sent")
        self.assertEqual(views[0].compare.name, "P1_Reg.nii.gz")

    def test_with_no_declaration_the_acquisition_still_anchors(self):
        """The ordinary modes, where the fixed scan IS what the caller sent."""
        cases = index.build(self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz"]))

        views = cases[0].views(acquisition="Scans", registered=True)

        self.assertEqual(views[0].anchor.name, "P1_T1.nii.gz")

    def test_a_declared_anchor_that_is_not_there_falls_back(self):
        """A manifest naming a file the archive does not hold -- a partial
        download, a step whose output was cleaned. The comparison falls back
        to the acquisition rather than the case vanishing."""
        cases = index.build(self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz"]))
        cases[0].registered_onto = "a_file_nobody_has.nii.gz"

        views = cases[0].views(acquisition="Scans", registered=True)

        self.assertEqual(views[0].anchor.name, "P1_T1.nii.gz")
        self.assertEqual(views[0].compare.name, "P1_Reg.nii.gz")

    def test_a_declared_anchor_naming_a_transform_is_not_an_anchor(self):
        """Only volumes are considered: a `.tfm` has no geometry to draw on,
        and anchoring on it would be an empty picture."""
        cases = index.build(
            self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz", "P1_Reg.tfm"]))
        cases[0].registered_onto = "P1_Reg.tfm"

        views = cases[0].views(acquisition="Scans", registered=True)

        self.assertEqual(views[0].anchor.name, "P1_T1.nii.gz")

    def test_a_report_that_is_not_json_is_ignored(self):
        """A truncated write. The names still key by the old rule."""
        folders = self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz"])
        with open(os.path.join(self.root, "produced", "Tool_report.json"),
                  "w", encoding="utf-8") as handle:
            handle.write('{"cases": {"P1"')

        cases = index.build(folders)

        self.assertEqual(sorted(case.key for case in cases),
                         ["P1_Reg", "P1_T1"])

    def test_a_report_writing_cases_as_a_list_is_ignored(self):
        """AMASSS's shape, keyed on an anonymous `p_000`. Read as the mapping
        the contract states, every file would key to the wrong patient."""
        folders = self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz"],
                               manifest=False)
        with open(os.path.join(self.root, "produced", "Tool_report.json"),
                  "w", encoding="utf-8") as handle:
            json.dump({"cases": [{"case_id": "p_000",
                                  "produced": ["P1_Reg.nii.gz"]}]}, handle)

        cases = index.build(folders)

        self.assertNotIn("p_000", [case.key for case in cases])

    def test_a_manifest_naming_files_nobody_has_invents_no_case(self):
        """A report from a run whose outputs were cleaned. A case with no
        artifact is a row in a picker that opens nothing."""
        folders = self._folders(["P1_T1.nii.gz"], [], manifest=False)
        with open(os.path.join(self.root, "produced", "Tool_report.json"),
                  "w", encoding="utf-8") as handle:
            json.dump({"cases": {"GHOST": {"produced": ["nothing.nii.gz"]}}},
                      handle)

        cases = index.build(folders)

        self.assertNotIn("GHOST", [case.key for case in cases])

    def test_a_case_id_that_is_a_path_does_not_become_one(self):
        """A manifest is a tool's word and still not a path: an id with
        separators in it must not silently key a case into a subfolder the
        picker then groups by."""
        folders = self._folders(["P1_T1.nii.gz"], ["P1_Reg.nii.gz"],
                               manifest=False)
        with open(os.path.join(self.root, "produced", "Tool_report.json"),
                  "w", encoding="utf-8") as handle:
            json.dump({"cases": {"../../escaped": {
                "produced": ["P1_Reg.nii.gz"]}}}, handle)

        cases = index.build(folders)
        keys = [case.key for case in cases]

        self.assertNotIn("../../escaped", keys)
