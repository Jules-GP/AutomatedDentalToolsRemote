---
name: modernize-module-ui
description: Rebuild a legacy Slicer module's interface - a hand-written .ui file plus copy-pasted stylesheets - as a thin schema-driven panel on ServerToolWidgetBase. Use when converting Agent, CLIC, MedX, VFACE, MRI2CBCT, CNE, AutoCrop3D or Medical_Data_Anonymizer, or when a module still carries its own CSS, its own QStackedWidget or its own anatomy.
---

# Converting a module to the generated panel

Two populations of module live in this repository.

**Converted** - ALI, AMASSS, AREG, ASO, AutoMatrix, DOCShapeAXI, ExampleTool,
FlexReg, GreedyReg, SurgMovPred, ServerToolsSettings, SlicerCloud. A subclass of
`ServerToolWidgetBase` declaring `TOOL_NAME` and little else: 41 lines for
`ExampleTool`, 143 for `AMASSS`. No `.ui`, no CSS, no anatomy.

**Not converted** - Agent, CLIC, MedX, VFACE, MRI2CBCT, CNE, AutoCrop3D,
Medical_Data_Anonymizer_Module, and every `*_CLI` folder. 600 to 2400 lines each,
mostly a `Resources/UI/*.ui`, and up to twenty `setStyleSheet` calls repeating
the same 260 lines of CSS that `design.py` exists to replace.

A `.ui` file is **not** how you tell the two apart: ALI, AMASSS, AREG, ASO,
AutoMatrix and DOCShapeAXI are converted and still carry theirs, unwired (see
§5). The test is `setStyleSheet` in the module, and whether the widget subclasses
`ServerToolWidgetBase`.

Converting one is the job below.

## 0. Precondition: the tool has to exist server-side

The panel is generated from `GET /tools`. If the module's tool is not served,
there is nothing to generate from and this is not the right skill - the tool has
to be packaged first (`migrate-tool`, in the `VISOR-serve` repo). Check:

```bash
curl -s http://localhost:8001/tools | python3 -c 'import json,sys; print(sorted(t["name"] for t in json.load(sys.stdin)))'
```

## 1. Inventory what the old interface actually offers

Do this before deleting anything, and read the `.ui` rather than the Python -
half the fields are never mentioned in the widget code.

```bash
M=MedX   # the module being converted
grep -oE 'name="[^"]+"' $M/Resources/UI/$M.ui | sort -u
grep -n 'self\.ui\.[A-Za-z_]*' $M/$M.py | grep -oE 'self\.ui\.[A-Za-z_]+' | sort -u
grep -c setStyleSheet $M/$M.py
```

Sort every field into three piles and **write the piles down**:

- **carried by the schema** - the tool already declares it; nothing to do but
  check the label and section.
- **deliberately dropped**, with the reason. Most of what a legacy widget holds
  is laptop-era machinery that a server makes meaningless: install buttons for
  pytorch, RAM watchdogs, queue tables, "free memory" buttons, cool-downs
  between scans, progress theatre. Drop it and say so.
- **missing from the schema** - the only real work, and it is work in
  `SADT-VISOR`, not here: the argument has to exist on the tool before a panel
  can show it.

## 2. Move the knowledge to the side that owns it

The rule that makes the conversion worth doing: **nothing about the anatomy, the
catalogs or the wording stays in the widget.** A structure list, a landmark
catalog, a tooth numbering, a per-mode "Suggest" default, the text of a label -
all of it is the tool's, and belongs in that tool's `layout.py` where it is
derived from the same table the tool computes with. That is what buys "a
landmark added to a catalog gets its tab with no client release".

Mode-switching interfaces get special attention. A legacy `QStackedWidget` with
one page per mode becomes `visible_when` (hide the inert half) and
`options_when` (narrow a choice's own options). See the `slicer-panel-layout`
skill for the vocabulary.

## 3. Rewrite the module

Copy the closest converted module and keep the shape:

- the `ScriptedLoadableModule` subclass - title, categories,
  `dependencies = ["ServerToolsCore"]`, contributors, help and acknowledgement
  text - kept **verbatim** from the old module. This is attribution and funding
  text; do not paraphrase it.
- the widget: `class XWidget(ServerToolWidgetBase)` with `TOOL_NAME`, plus
  `FILE_INPUTS` / `RESULT_KIND` only where the schema genuinely cannot say
  (a `volume_node` picker, whether a returned file is loaded or saved).
- hooks only where needed: `configureFields`, `addExtraWidgets`, `collectArgs`,
  `handleResult`.

Delete, and expect to delete a lot: `setup()`'s widget plumbing, every
`setStyleSheet`, `_isDarkMode`/`applyDarkModeStyles`, the manual `.ui` loading,
the per-field `connect` boilerplate, the hard-coded "are all required fields
filled" check, the `TEST_DATA` dict of GitHub release URLs (test files now come
from `GET /tools/{tool}/data`, for every tool, declared by nobody), and any
thread or progress dialog of the module's own - `worker.py` owns that.

## 4. What must not be lost in the conversion

Check each one explicitly; these are the regressions that look like nothing:

- **starting values.** A spin box the user never touches still sends its value.
  If the old `.ui` had a default, the schema needs `initial` - otherwise Qt's
  zero travels in place of the tool's default.
- **file-dialog filters.** The old picker offered `.nii/.nii.gz/.nrrd`; without
  `extensions` on the schema's path argument the new one falls back to the
  server's whole `ALLOWED_EXTENSIONS`.
- **optional versus required wording.** An empty picker reads as a demand
  whatever the label says; an optional file argument gets
  `design.optional_label`, which says "(optional)" in words.
- **help text and tooltips.** The schema's per-argument `description` becomes the
  tooltip. Anything the old UI explained and the schema does not is lost silently.
- **acknowledgements and contributors.** See §3.

## 5. Verify, then delete

```bash
# 1. Core still green
cd ServerToolsCore/Testing/Python && python3 -m unittest test_formgen test_runs test_client

# 2. A client test for the converted module, next to its peers. Both forms work.
python3 -m unittest <Module>/Testing/Python/test_<module>_client.py
```

Follow `ALI/Testing/Python/test_ali_client.py` for the pattern, including what it
refuses to do: it does **not** import `ALI.py`, which needs a real Slicer, and
asserts the module's declarations against the functions it delegates to instead -
stubbing Slicer's module framework would only measure the stub.

Then in Slicer, against a running server: the panel builds, every input picks a
file, Apply runs, Cancel interrupts, the result lands in the scene, and the
whole thing is checked in **light and dark**.

**Unwire the old files; do not delete them in the same change.** The convention
here is visible in ALI, ASO, AREG and AutoMatrix: `<Module>_Method/` and
`Resources/UI/<Module>.ui` stay in the tree, no longer wired to anything, with a
line in the module docstring saying exactly that. It is the fastest rollback
there is while the conversion is unproven. Removing them - and their
`CMakeLists.txt` entries - is a separate, later decision.

## 6. Done looks like

- [ ] The module file is under ~250 lines and mentions no color, no anatomy.
- [ ] `grep -c setStyleSheet <Module>/<Module>.py` returns 0.
- [ ] The old `.ui` and `_Method/` are unwired, and the docstring says so.
- [ ] Every dropped feature has a written reason.
- [ ] Nothing from §4 was lost.
- [ ] Verified in Slicer, both themes, against a real server.
