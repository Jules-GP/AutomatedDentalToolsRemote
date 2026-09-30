"""What a folder of results holds, grouped so a reader can step through it.

The panel's job is to put a scan on screen with the landmarks, masks and
surfaces that belong to it. Nothing in this extension could answer which file
belongs to which, so this module does: it walks the folders it is given,
groups their files by patient, and says which anchor each overlay may be drawn
against.

**Why the grouping cannot be `os.listdir` and a sort.** Measured against the
tools as they are written today, four naming conventions coexist:

* `ALI`, `Crown_Seg` and `FlexReg` APPEND to the input stem, so
  `P1_scan.nii.gz` becomes `P1_scan_lm_Pred.mrk.json`;
* `ASO` CBCT TRUNCATES it -- the same input becomes `P1_Or.nii.gz` and
  `P1_lm_Or.mrk.json`, sharing no stem at all with ALI's output;
* `ASO` IOS writes its surface, its landmarks and its transform under THREE
  different stems, sharing only a patient-and-jaw key;
* `Crown_Seg` files half a batch under an extra `crownseg_input_<suffix>/`
  level and the other half beside it, and `AMASSS` invents a
  `<stem>_<id>_SegOut/` folder per scan.

Truncating at a known tool suffix, on token boundaries, is what reconciles the
first two -- and it is what `ASO` itself already does to read `ALI`'s output
back. `_normalise_directory` handles the last one.

**This is a third copy of an algorithm, deliberately.** The reference lives in
`SADT-VISOR` at `tools/AREG/common/src/sadt_areg_common/pairing.py`, and `ASO`
carries a second, older copy whose suffix table is missing `_Seg` -- so the two
already disagree on `arch_Seg.vtk`. A client cannot import either: they are
inside tool virtualenvs in another repository. The copy ends the day a run
publishes a manifest naming its own outputs, which is the only source that can
be right by construction rather than by guessing at names.
"""

import json
import os
import re

# Longest first: `.nii.gz` must never resolve as `.gz`, and `.nrrd.gz` never as
# `.gz` either. The list is the union of what the tools read and write.
SCAN_EXTENSIONS = (".nii.gz", ".nrrd.gz", ".gipl.gz", ".nii", ".nrrd", ".gipl")
SURFACE_EXTENSIONS = (".vtk", ".vtp", ".stl", ".obj", ".off")
MARKUPS_EXTENSIONS = (".mrk.json",)
# ASO IOS's landmarks are a BARE `.json` on the hosted test data
# (`Upper_new_9_Upper_O_Pred.json`), so extension alone under-counts them and
# over-counts every other json in the folder. The head of the file settles it.
_MARKUPS_AMBIGUOUS_EXTENSION = ".json"
_MARKUPS_MARKER = '"markups"'
_MARKUPS_SNIFF_BYTES = 512
TRANSFORM_EXTENSIONS = (".tfm", ".h5")

# What a file IS, in the vocabulary `slicer_io` already loads by.
VOLUME = "volume"
LABELMAP = "labelmap"
MODEL = "model"
MARKUPS = "markups"
TRANSFORM = "transform"

# An anchor is something an overlay can be drawn ON. A markups file has no
# geometry of its own and a transform is not viewable at all.
ANCHOR_KINDS = (VOLUME, LABELMAP, MODEL)
# What a case can be BUILT on: something with a shape of its own. A labelmap
# is not here, and that is the difference -- it anchors a view when nothing
# else does, but it is derived from a scan and belongs to one.
GEOMETRY_KINDS = (VOLUME, MODEL)
OVERLAY_KINDS = (MARKUPS, LABELMAP, MODEL)

# Truncated at the FIRST of these that ends on a token boundary. The table is
# the superset from AREG's `pairing.py`; `_Seg`/`_seg` are the two entries ASO's
# own copy lacks, and leaving them out is what makes `arch_Seg.vtk` and
# `arch_Seg_lm_Pred.mrk.json` look like two different patients.
PATIENT_SUFFIXES = (
    "_lm_Pred", "_Scanreg", "_MERGED", "_OutReg", "_SegOr",
    "_scan", "_Scan", "_Seg", "_seg", "_Or", "_OR", "_lm",
)

# Dropped from a stem only when the caller asks. The viewer does NOT: two
# timepoints of one patient are two scans to step through, and collapsing them
# would hide one behind the other.
TIMEPOINT_TOKENS = ("t1", "t2", "t0")

# A scan whose stem carries one of these is shown as labelled voxels rather
# than greyscale. Whole tokens, never substrings: `"max" in name` makes a
# patient called MAX_01 a maxillary mask.
#
# This disagrees with AutoCrop3D on purpose. That tool declares its output a
# volume even when it cropped a segmentation, because a cropped segmentation is
# still whatever the caller sent; here the question is only how to draw it, and
# `P1_seg_cropped.nii.gz` is more useful drawn as labels.
MASK_TOKENS = ("mask", "seg", "segmentation", "pred", "label", "labels", "merged")

# Skipped whole. `.amasss_work/` and `.batchdentalseg_work/` are scratch
# directories created INSIDE the output folder and removed in a `finally` -- so
# they are only ever present after a run was killed, holding nnUNet
# intermediates named `p_000_0000.nii.gz` that are not anybody's patient.
_HIDDEN_PREFIX = "."

# Every tool writes one, none of them is viewable, and `run_report.json` would
# otherwise index as a patient called `run`.
_REPORT_NAMES = ("run_report.json",)
_REPORT_SUFFIX = "_report.json"

# A DICOM series is a DIRECTORY of single-slice files -- 577 of them for one
# scan on the hosted ASO fixture -- and Slicer reads the whole series from the
# folder. Indexed as one volume named after the folder, which is also the only
# place that scan's patient name appears: the slices are `IMG0375.dcm`.
_DICOM_EXTENSION = ".dcm"

_SEPARATORS = re.compile(r"[_\-.\s]+")
_SEPARATOR = re.compile(r"[_\-.\s]")

# `crownseg_input_<suffix>/` for the meshes Crown_Seg segmented itself, nothing
# for the ones it passed through -- so one batch lands at two depths. It names
# no patient: the files inside keep their own stems.
_EXTRA_LEVELS = (re.compile(r"^crownseg_input(_.+)?$"),)

# AMASSS is the other way round: `<scan stem>_<prediction_ID>_SegOut/` is the
# ONLY place the scan's name survives whole. Inside it every file is
# `<stem>_<prediction_ID>_<CODE>`, and `prediction_ID` is free text a caller
# passes -- so no table of suffixes can strip it off a file name, while the
# folder gives it up to a greedy match.
_DECLARING_LEVEL = re.compile(r"^(?P<base>.+)_.+_SegOut$")

# A folder that says what KIND of file is in it rather than whose it is:
# `<root>/CBCT/` beside `<root>/Landmarks/` is one cohort filed by role, not
# two cohorts. Stripped from the case key so a patient's scan and its points
# are one case -- and remembered on the artifact, because whether two files
# sit in the same real directory is what decides whether the points may be
# drawn on that scan without a caveat.
ROLE_TOKENS = frozenset((
    "cbct", "ios", "scan", "scans", "volume", "volumes", "surface", "surfaces",
    "mesh", "meshes", "landmark", "landmarks", "markup", "markups",
    "mask", "masks", "segmentation", "segmentations", "seg", "segs",
    "transform", "transforms", "output", "outputs", "result", "results",
    # A bare `T1/` or `T2/` level too. It looks like a timepoint and it is
    # one, but the timepoint is already in the stems under it -- the hosted
    # AREG fixture files `P_0001_T2.nii.gz` under `T2/` -- so the level adds
    # nothing and splits the case away from its landmarks. Where two
    # timepoints DID share a stem they become one case holding both, which a
    # viewer shows as two views of one subject rather than as two subjects.
    "t0", "t1", "t2",
))


def is_role_level(part: str) -> bool:
    """Is this directory named after a KIND of file rather than after a subject?

    Every token has to be one, which is what keeps a patient out. The hosted
    AREG fixture is why it is not a list of exact names: it files one case
    under `CBCT Landmarks/`, `IOS Landmarks/` and `T2/`, and matching
    `Landmarks` alone left the same patient indexed three times.

    A level is only a role when EVERY token is one, so `patient_T1/` stays a
    patient while `T1/` does not.
    """
    parts = tokens(part)
    return bool(parts) and all(token.lower() in ROLE_TOKENS for token in parts)


def split_extension(filename: str) -> tuple:
    """`("P1_scan", ".nii.gz")`, compound extensions kept whole."""
    lowered = filename.lower()
    for extension in (
        MARKUPS_EXTENSIONS + SCAN_EXTENSIONS + SURFACE_EXTENSIONS + TRANSFORM_EXTENSIONS
    ):
        if lowered.endswith(extension) and len(filename) > len(extension):
            return filename[: -len(extension)], filename[-len(extension):]
    stem, extension = os.path.splitext(filename)
    return stem, extension


def tokens(stem: str) -> tuple:
    """The stem's words, separators dropped: `P1_scan_lm` -> `("P1","scan","lm")`."""
    return tuple(part for part in _SEPARATORS.split(stem) if part)


def _token_aligned_index(stem: str, suffix: str) -> int:
    """Where `suffix` starts in `stem`, or -1 if it never sits on a boundary.

    A plain `find` is what collapsed `P_Seg1_T1` and `P_Seg2_T1` onto one
    patient upstream: `_Seg` matches inside `_Seg1`, two subjects become one,
    and one of them is silently lost. The match has to END on a separator or at
    the end of the stem; every entry of `PATIENT_SUFFIXES` already begins with
    one, which is what settles the other side.
    """
    start = 0
    while True:
        index = stem.find(suffix, start)
        if index < 0:
            return -1
        end = index + len(suffix)
        if end == len(stem) or _SEPARATOR.match(stem[end]):
            return index
        start = index + 1


def _drop_tokens(stem: str, unwanted) -> str:
    kept = [token for token in tokens(stem) if token.lower() not in unwanted]
    return "_".join(kept) if kept else stem


def patient_stem(filename: str, drop_timepoint: bool = False) -> str:
    """The patient a file is about, whichever tool named it.

    `P1_scan.nii.gz`, `P1_scan_lm_Pred.mrk.json`, `P1_Or.nii.gz` and
    `P1_Pred_MERGED.nii.gz` all answer `P1`.
    """
    stem, _ = split_extension(filename)
    cut = len(stem)
    for suffix in PATIENT_SUFFIXES:
        index = _token_aligned_index(stem, suffix)
        if 0 <= index < cut:
            cut = index
    # Never truncate to nothing: a file literally named `_Or.nii.gz` is a
    # patient whose name we cannot read, not a patient with no name.
    trimmed = stem[:cut] or stem
    return _drop_tokens(trimmed, TIMEPOINT_TOKENS) if drop_timepoint else trimmed


def kind_of(filename: str, path: str = ""):
    """Which Slicer node the file opens as, or None for one that opens as none.

    `path` is only read for a bare `.json`, where the extension does not say.
    """
    lowered = filename.lower()
    if lowered.endswith(MARKUPS_EXTENSIONS):
        return MARKUPS
    if lowered.endswith(_MARKUPS_AMBIGUOUS_EXTENSION):
        return MARKUPS if path and looks_like_markups(path) else None
    if lowered.endswith(SURFACE_EXTENSIONS):
        return MODEL
    if lowered.endswith(TRANSFORM_EXTENSIONS):
        return TRANSFORM
    if lowered.endswith(SCAN_EXTENSIONS):
        stem, _ = split_extension(filename)
        lowered_tokens = {token.lower() for token in tokens(stem)}
        return LABELMAP if lowered_tokens & set(MASK_TOKENS) else VOLUME
    return None


def looks_like_markups(path: str) -> bool:
    """Does this `.json` open as landmarks? Read the head rather than guess.

    Cheap -- a few hundred bytes -- and it is the difference between showing
    ASO IOS's points and showing none of them, its writer having dropped the
    `.mrk` half of the extension on the data we have.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(_MARKUPS_SNIFF_BYTES)
    except OSError:
        return False
    return _MARKUPS_MARKER.encode() in head


def _is_report(filename: str) -> bool:
    return filename in _REPORT_NAMES or filename.endswith(_REPORT_SUFFIX)


def _normalise_directory(relative: str) -> tuple:
    """`(directory, declared patient or "")` for a walked path.

    Three kinds of level are removed. A ROLE level (`CBCT/`, `Landmarks/`,
    `IOS Landmarks/`) is how a reader files a cohort, not who is in it. The other two a tool
    invented, and they are removed for opposite reasons. Crown_Seg's carries nothing -- dropping it is what stops
    a mesh it segmented from indexing two directories away from one it passed
    through. AMASSS's carries EVERYTHING: the scan's stem is in the folder name
    and nowhere else recoverable, its files being `<stem>_<prediction_ID>_<CODE>`
    with a free-text id in the middle.
    """
    parts = [part for part in relative.split(os.sep) if part not in ("", ".")]
    kept, declared = [], ""
    for part in parts:
        if is_role_level(part):
            continue
        if any(pattern.match(part) for pattern in _EXTRA_LEVELS):
            continue
        match = _DECLARING_LEVEL.match(part)
        if match:
            declared = match.group("base")
            continue
        kept.append(part)
    return os.sep.join(kept), declared


def is_token_prefix(prefix: str, stem: str) -> bool:
    """Is `prefix` the start of `stem`, ending on a token boundary?

    What pairs `Upper_new_9_Upper_O_Pred.json` with `Upper_new_9.vtk` -- ASO
    IOS being the one producer whose landmark stem is not its surface stem.
    The boundary is what keeps patient `P1` off patient `P10`, which is the
    substring match that paired them upstream.
    """
    if not prefix or len(prefix) >= len(stem):
        return False
    return stem.startswith(prefix) and bool(_SEPARATOR.match(stem[len(prefix)]))


class Artifact:
    """One file on disk, and what is known about it without opening it."""

    __slots__ = ("path", "kind", "source", "directory", "origin", "name", "patient")

    def __init__(self, path: str, kind: str, source: str, directory: str,
                 patient: str, origin: str = ""):
        self.path = path
        self.kind = kind
        self.source = source
        # Where the case is filed: role levels stripped, so a scan and its
        # landmarks meet.
        self.directory = directory
        # Where the file really is. Two artifacts share it only when they were
        # written side by side, which is the evidence `View` needs and the one
        # thing `directory` can no longer answer.
        self.origin = origin if origin else directory
        self.name = os.path.basename(path)
        self.patient = patient

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<Artifact {self.name} {self.kind} from {self.source}>"


class View:
    """One anchor and the overlays that are in ITS frame.

    A case can hold several: `ASO` writes an oriented scan beside its
    landmarks, and the ORIGINAL scan is still in the input folder. Both are
    worth looking at and they are not the same picture -- so they are two
    views, never one anchor with everybody's points on it.
    """

    # `basis` is reassigned when a view gains overlays it did not start with,
    # so the two travel together and a caller cannot read one without the other.
    #
    # `compare` is a SECOND scan in this view's frame, or None. Kept apart from
    # `overlays` because it is not one: an overlay is drawn ON the anchor --
    # points, a mask, a surface -- while this is another greyscale volume the
    # anchor is compared against, and a viewer puts it in a different place
    # (the slice views' foreground layer, with a blend the reader controls).
    __slots__ = ("anchor", "overlays", "basis", "compare")

    def __init__(self, anchor, overlays, basis: str, compare=None):
        self.anchor = anchor
        self.overlays = list(overlays)
        self.basis = basis
        self.compare = compare

    @property
    def label(self) -> str:
        return self.anchor.name if self.anchor is not None else "(no scan)"


# What `View.basis` says, and it is shown to the reader rather than kept here.
#
# The distinction is the whole reason this class exists. ASO's landmarks carry
# the recentring AND the ICP rotation, so they match the scan ASO wrote and not
# the one it read; ALI writes no scan at all, so its landmarks can only be
# drawn on the input -- where they are correct. Draw either on the other and
# the result renders without an error and is wrong by a rotation, which is
# exactly the mistake a reviewer is looking at the screen to catch.
BASIS_COLOCATED = "written beside this scan"
BASIS_ACQUISITION = "assumed: the acquisition"
BASIS_NONE = "no scan found"
# Two scans in ONE picture, which every other basis here exists to refuse.
# See `View`: an oriented scan and the one it was made from are NOT the same
# picture, and drawing either on the other is wrong by a rotation and renders
# without an error -- the exact mistake a reviewer is at the screen to catch.
#
# A REGISTRATION is the one case where it is honest, and honest by
# construction: the result was resampled into the target's frame, which is
# what registering means. Nothing here can check that -- this module reads
# names, not NIfTI headers -- so it is not inferred. The tool that wrote the
# file declares it, as `review_kind = "registration"`, and the caller passes
# it down. A caller that says nothing gets the old behaviour: two views.
BASIS_REGISTERED = "registered onto this scan"


class Case:
    """Everything one patient has, across every folder that was indexed."""

    __slots__ = ("key", "artifacts")

    def __init__(self, key: str):
        self.key = key
        self.artifacts = []

    @property
    def patient(self) -> str:
        return os.path.basename(self.key)

    @property
    def label(self) -> str:
        """How the case reads to a person.

        `key` is an identity and is built to be unique -- it carries the
        subfolder, because two patients of the same name in two folders are
        two patients. That makes it a path, and a path read as a name is
        noise: what a reader wants first is WHO, with where it was found as
        context behind it.
        """
        folder = os.path.dirname(self.key)
        return f"{self.patient}  ({folder})" if folder else self.patient

    def of_kind(self, *kinds) -> list:
        return [artifact for artifact in self.artifacts if artifact.kind in kinds]

    def _registered_onto(self, acquisition: str):
        """One view: the acquisition, with the registered result compared to it.

        None unless this case really has both -- a scan from the acquisition
        and a scan from somewhere else. A case the run produced nothing for,
        or one whose only volume IS the acquisition, falls through to the
        ordinary pictures rather than being shown an empty comparison.

        Overlays follow the ANCHOR, as everywhere else in this class: what was
        written beside the acquisition is drawn on it, and what the result
        brought with it -- a mask, a transform's own outputs -- is drawn on it
        too, because the two are in one frame and that is the premise.
        """
        volumes = [artifact for artifact in self.of_kind(VOLUME)]
        sent = [artifact for artifact in volumes if artifact.source == acquisition]
        produced = [artifact for artifact in volumes if artifact.source != acquisition]
        if not sent or not produced:
            return None
        overlays = [artifact for artifact in self.of_kind(*OVERLAY_KINDS)
                    if artifact is not sent[0] and artifact is not produced[0]]
        return View(sent[0], overlays, BASIS_REGISTERED, compare=produced[0])

    def views(self, acquisition: str = "", registered: bool = False) -> list:
        """The pictures this case can honestly produce.

        One per anchor, holding the overlays written beside it. Overlays with
        no anchor in their own folder fall back to the acquisition's scan and
        say so -- that is `ALI`, whose landmark file is alone in its directory
        and belongs to the scan the caller sent.

        `registered` says the tool declared what it wrote to be a
        registration, so its scan is in the acquisition's frame and the two
        belong in ONE picture -- the acquisition anchoring it, the result
        compared against it. Off by default and off for everything else: see
        `BASIS_REGISTERED` for why this is declared rather than detected.
        """
        if registered and acquisition:
            paired = self._registered_onto(acquisition)
            if paired is not None:
                return [paired]
        anchors = self.of_kind(*ANCHOR_KINDS)
        overlays = self.of_kind(*OVERLAY_KINDS)
        # A labelmap and a surface are both: they anchor a view of their own and
        # they are drawn on the greyscale scan beside them. Only a scan is never
        # somebody else's overlay.
        volumes = [artifact for artifact in anchors if artifact.kind == VOLUME]

        views = []
        placed = set()
        for anchor in volumes or anchors:
            beside = [
                artifact for artifact in overlays
                if artifact is not anchor
                and (artifact.source, artifact.origin) == (anchor.source, anchor.origin)
            ]
            placed.update(id(artifact) for artifact in beside)
            views.append(View(anchor, beside, BASIS_COLOCATED))

        anchored = {id(view.anchor) for view in views}
        homeless = [
            artifact for artifact in overlays
            if id(artifact) not in placed and id(artifact) not in anchored
        ]
        if not homeless:
            return views

        fallback = next(
            (view for view in views if view.anchor.source == acquisition), None
        ) or (views[0] if views else None)
        if fallback is None:
            return views + [View(None, homeless, BASIS_NONE)]
        if not fallback.overlays:
            # The same picture, so the same view. Appending a second one on
            # the same anchor put the points behind a drop-down the reader had
            # no reason to open -- and the empty view came first.
            fallback.overlays.extend(homeless)
            fallback.basis = BASIS_ACQUISITION
        else:
            # That anchor already carries overlays written beside it, and
            # these were not. Two claims about the frame cannot share a line,
            # so they do not share a view.
            views.append(View(fallback.anchor, homeless, BASIS_ACQUISITION))
        return views


# What a case with no directory of its own is called in the picker. A blank
# chip is unclickable and reads as a bug.
AT_THE_TOP = "(at the top)"


def folder_of(case) -> str:
    """The first level under the folder that was opened, or `AT_THE_TOP`.

    What a reader picks between when one folder holds several cohorts: the
    hosted ASO fixture is eight subjects across `CBCT_SemiAuto/`,
    `IOS_FullyAuto/` and four others, and wanting to look at the CBCT ones is
    not a reason to open a different folder.

    The FIRST level and not the whole path: deeper levels are how a tool
    mirrors an input tree, and offering one chip per patient would be a
    second case list.
    """
    directory = os.path.dirname(case.key)
    return directory.split(os.sep)[0] if directory else AT_THE_TOP


def folders_in(cases) -> list:
    """The levels those cases came from, in the order they are listed."""
    found = []
    for case in cases:
        name = folder_of(case)
        if name not in found:
            found.append(name)
    return found


def _stated_cases(root: str) -> dict:
    """`{path or file name: case id}`, from a run report that says so.

    **The end of the guessing, where a run says out loud what it produced.**
    This module's own first paragraph calls itself "a third copy of an
    algorithm, deliberately" and names the day it ends: the day a run
    publishes a manifest naming its own outputs. Eleven tools of the
    catalogue publish `{"cases": {<id>: {"produced": [...], "inputs": {...}}}}`
    and this reads it.

    Names cannot carry it, and not for want of a better table. AREG's output
    suffix is an ARGUMENT -- `output_suffix="Reg"` by default and whatever the
    caller asks for otherwise -- so `P1_T1.nii.gz` becomes `P1_Reg.nii.gz` or
    `P1_aligned.nii.gz` at the caller's choice, and no static list of markers
    can reconcile the two. Measured on 2026-09-30: indexed by name, one
    patient's input and output keyed as `P1_T1` and `P1_Reg`, two cases, and
    the comparison between them had nothing to stand on.

    Both spellings are offered for one reason: a manifest names a case's
    outputs relative to the OUTPUT folder and its inputs relative to whichever
    argument they came from, and this function is handed one root at a time
    with no way to tell which is which. The relative path is tried first and
    the bare file name second, which within one run is unambiguous.

    `{}` for a folder with no report, an unreadable one, or one in an older
    vocabulary -- and the caller then keys by name exactly as before.
    """
    stated = {}
    for filename in sorted(os.listdir(root)) if os.path.isdir(root) else ():
        if not _is_report(filename):
            continue
        try:
            with open(os.path.join(root, filename), encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            continue
        cases = document.get("cases")
        if not isinstance(cases, dict):
            continue
        for case, entry in cases.items():
            if not isinstance(case, str) or not isinstance(entry, dict):
                continue
            paths = list(entry.get("produced") or ())
            paths.extend(
                path for path in (entry.get("inputs") or {}).values()
                if isinstance(path, str)
            )
            for path in paths:
                if not isinstance(path, str) or not path:
                    continue
                stated.setdefault(path.replace("\\", "/"), case)
                stated.setdefault(os.path.basename(path), case)
    return stated


def build(sources, drop_timepoint: bool = False) -> list:
    """Index `sources` -- `[(label, root), ...]` -- into cases, ordered by key.

    `label` is how the reader named the folder ("Scans", "Results"); it travels
    with every artifact so a view can say which folder its anchor came from,
    and so the acquisition can be preferred when an overlay has no anchor of
    its own.
    """
    cases = {}
    # Every root's manifest, merged, BEFORE any of them is walked. One run
    # writes one report and it sits in the output folder, so reading it per
    # root left the input folder with nothing to go on -- and the input is
    # exactly the half that has to be keyed the same way for a case to hold
    # both. Merged, a report in any source speaks for all of them, which is
    # what one run over several folders actually is.
    stated = {}
    for _label, root in sources:
        if root and os.path.isdir(root):
            for path, case in _stated_cases(root).items():
                stated.setdefault(path, case)
    for label, root in sources:
        if not root or not os.path.isdir(root):
            continue
        for directory, subdirectories, filenames in os.walk(root):
            subdirectories[:] = sorted(
                name for name in subdirectories if not name.startswith(_HIDDEN_PREFIX)
            )
            raw = os.path.relpath(directory, root)
            raw = "" if raw == "." else raw
            relative, declared = _normalise_directory(raw)

            if any(name.lower().endswith(_DICOM_EXTENSION) for name in filenames):
                # One volume, named after the folder, filed with its PARENT --
                # which is where ASO's fixture keeps the landmarks that go with
                # it. Nothing below a series is indexed: its slices are not
                # patients.
                subdirectories[:] = []
                parent, series = os.path.split(relative)
                patient = patient_stem(series or os.path.basename(root),
                                       drop_timepoint=drop_timepoint)
                key = os.path.join(parent, patient) if parent else patient
                case = cases.setdefault(key, Case(key))
                case.artifacts.append(
                    Artifact(directory, VOLUME, label, parent, patient,
                             origin=os.path.dirname(raw))
                )
                continue

            for filename in sorted(filenames):
                if filename.startswith(_HIDDEN_PREFIX) or _is_report(filename):
                    continue
                full = os.path.join(directory, filename)
                kind = kind_of(filename, full)
                if kind is None:
                    continue
                # The run's own word outranks everything: a manifest states
                # which case a file belongs to, and the id IS the key -- NOT
                # joined to the directory, or `CB/C_0001` and `T1/C_0001`
                # stay the two cases this exists to make one.
                inside = os.path.relpath(full, root).replace(os.sep, "/")
                spoken = stated.get(inside) or stated.get(filename)
                if spoken:
                    patient, key = spoken, spoken
                else:
                    # A declaring folder outranks the file name: it is the only
                    # place AMASSS's scan stem survives the prediction id.
                    patient = patient_stem(
                        declared or filename, drop_timepoint=drop_timepoint
                    )
                    key = os.path.join(relative, patient) if relative else patient
                case = cases.setdefault(key, Case(key))
                case.artifacts.append(
                    Artifact(full, kind, label, relative, patient, origin=raw)
                )

    return [cases[key] for key in sorted(_absorb_orphans(cases))]


def _absorb_orphans(cases: dict) -> dict:
    """Fold a case that is only overlays into the anchored case it belongs to.

    A suffix table cannot reach every producer: ASO IOS names its landmarks
    after the landmark FILE it was handed, so `Upper_new_9_Upper_O_Pred.json`
    keys nowhere near `Upper_new_9.vtk`. Prefix on a token boundary does reach
    it, and only within the same directory -- across directories the same
    prefix is two different patients.

    **What counts as needing a host is holding no GEOMETRY**, not holding no
    anchor. A mask anchors a view of its own and is still somebody's mask:
    AMASSS names one `<stem>_<prediction_ID>_MAND.nii.gz`, with a free-text id
    in the middle that no suffix table can strip, so loose in a folder it
    indexed as a patient called `p1_Pred_MAND` instead of joining `p1`.
    """
    anchored = sorted(key for key, case in cases.items() if case.of_kind(*GEOMETRY_KINDS))
    for key in sorted(cases):
        case = cases[key]
        if case.of_kind(*GEOMETRY_KINDS):
            continue
        directory = os.path.dirname(key)
        # Longest first: with `P1` and `P1_T1` both anchored, an overlay of
        # `P1_T1_lm` belongs to the more specific one.
        host = max(
            (other for other in anchored
             if os.path.dirname(other) == directory
             and is_token_prefix(os.path.basename(other), os.path.basename(key))),
            key=len, default=None,
        )
        if host is None:
            continue
        cases[host].artifacts.extend(case.artifacts)
        del cases[key]
    return cases
