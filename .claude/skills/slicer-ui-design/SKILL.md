---
name: slicer-ui-design
description: Change how the Slicer extension looks - colors, buttons, labels, spacing, dark mode, a widget that reads as foreign next to the others. Use when restyling a panel, adding a styled-widget factory, or reworking the extension's visual language. Covers design.py, the no-CSS-in-a-module rule, the PythonQt traps, and how to verify a look without launching Slicer.
---

# Restyling the extension

Every color, every pixel of spacing and every stylesheet in this extension is
supposed to come from one file:

    ServerToolsCore/ServerToolsCoreLib/design.py

The acceptance test for the whole layer is written down and still holds:
**changing the primary button color across the extension is an edit to one
line.** Any change that breaks that is the wrong change, however good it looks.

## 1. Before writing anything, find out where the look comes from

```bash
# Who still writes their own CSS? Only legacy modules and the test stubs may.
grep -rn 'setStyleSheet' --include='*.py' . | grep -v 'ServerToolsCoreLib/design.py'

# What does design.py already offer? Read the factories before adding one.
grep -n '^def \|^_[A-Z_]* = \|^[A-Z_]* = ' ServerToolsCore/ServerToolsCoreLib/design.py
```

A module on `ServerToolWidgetBase` (ALI, AMASSS, AREG, ASO, AutoMatrix,
DOCShapeAXI, ExampleTool, FlexReg, GreedyReg, SurgMovPred, ServerToolsSettings,
SlicerCloud) must produce **zero** hits. A hit there is the bug, and the fix is
a factory in `design.py`, not a stylesheet in the module. The remaining hits
(Agent, CLIC, MedX, VFACE, MRI2CBCT) are the not-yet-converted modules - see
the `modernize-module-ui` skill, and do not spot-fix their CSS.

## 2. Pick the smallest of the four changes

| What you want | Where it goes | Blast radius |
|---|---|---|
| A different color, everywhere | one value in `_LIGHT` **and** `_DARK` | the whole extension, which is the point |
| A different button color | one pair of stops in `_BUTTON_STOPS_LIGHT`/`_DARK` | every button of that role |
| A widget family that looks wrong (combo box, slider, progress bar) | a rule in `_base_stylesheet` | every module, since `design.apply()` runs on each panel root |
| A new kind of styled widget | a new factory next to `primary_button` / `hint_label` | only its callers |

**Both dicts, always.** `_LIGHT` and `_DARK` are parallel by key; a token added
to one and not the other is a `KeyError` in the other theme, which is exactly
the theme nobody tests in.

A new factory returns a configured widget and nothing else - no layout, no
signal wiring, no knowledge of what the panel is for. `link_button`,
`hint_label`, `toggle_button` are the shape to copy. Give it a docstring that
says **what role it plays**, not what it looks like: the existing ones explain
why a `link_button` is not a `primary_button` (three filled blue buttons above a
checkbox grid compete with Apply, the one button that starts a run), and that
reasoning is what stops the next person from reaching for the wrong one.

## 3. Restraint is a requirement, not a preference

The brief for this layer says it plainly: stay consistent with Slicer's native
look, prioritize legibility and consistency over originality, do not invent a
design language that clashes with the application hosting it.

Two concrete inheritances that are deliberate and must not be "cleaned up":

- The **vertical `qlineargradient`** on buttons. Every `.ui` of the original
  SlicerAutomatedDentalTools paints `QPushButton` with exactly that gradient;
  the flat fill that shipped here first read as a different product sitting next
  to those modules.
- The **flat** two-color toggle (`_TOGGLE_OFF` blue / `_TOGGLE_ON` red). Flat on
  purpose: the two-state color *is* the information, and a gradient would make
  it read as one more action button.

## 4. Dark mode

- `is_dark_mode()` reads `slicer.app.palette()` luminance and is the **only**
  place in the extension allowed to. Never add a second theme probe.
- `tokens()` re-reads the palette on every call, so factories and `apply()`
  always reflect the current mode.
- `design.apply(self.uiWidget)` runs at `setup()`, again at `enter()`, and again
  after a form rebuild (`base_widget.py:225,259,341`). A user who switches
  Slicer's theme sees it recompute when they re-enter the module. A **live**
  in-place recompute while the module is open and visible is not wired up and is
  a known limitation - do not claim you fixed it unless you actually wired a
  palette-change signal.
- Check both themes before calling a restyle done. A hard-coded hex outside the
  token dicts survives light mode and disappears in dark.

## 5. PythonQt traps, each one paid for already

This runs under PythonQt, not PyQt. These are not hypothetical:

- **`setCursor` needs a real `QCursor`.** `qt.QCursor(qt.Qt.PointingHandCursor)`,
  never the bare enum: PyQt converts implicitly, PythonQt does not reliably.
- **You cannot create an attribute on a C++ object.** `grid.columns = 2` raises
  "creating new attributes on C++ objects is not allowed" and takes the whole
  panel down. Read the value back from the schema instead (`formgen.build` does).
- **A `QScrollArea`'s size hint ignores its child**, so anything laid out inside
  one collapses to a few pixels. `CHART_MIN_HEIGHT` and `TABS_MIN_HEIGHT` in
  `design.py` are the floors that fix it - floors, not fixed heights.
- **No compiled Qt resources.** The checked-checkbox mark is an inline SVG data
  URI (`_CHECKMARK_SVG`), precisely so the extension needs no `.qrc` built. Keep
  new icons on that path.
- **A `QVBoxLayout` needs a trailing `addStretch(1)`** or it spreads its widgets
  down the whole panel instead of packing them at the top - the same reason every
  hand-written `.ui` in this repo ends with a vertical spacer.

## 6. Verify

```bash
# The GUI logic, with qt/ctk/slicer stubbed. Seconds, no Slicer.
cd ServerToolsCore/Testing/Python && python3 -m unittest test_formgen test_runs test_hosted_test_files
```

`qt_stubs.py` implements only what `formgen` and `design` actually touch. If a
new factory calls a Qt method the stubs lack, add it to the stub - a stub that
grows with the code is the point; a test skipped because the stub is thin is not.

Then, and only then, in Slicer: reload the module (`Reload` in the Reload &
Test section, or restart), and look at **both themes** and at least two panels -
one small (`Test_Tool`, `ExampleTool`) and one large (`ASO`, `ALI`), because
crowding only shows up on the large one.

## 7. Review checklist

- [ ] No `setStyleSheet` outside `design.py` in a converted module.
- [ ] Every new color exists in both `_LIGHT` and `_DARK`.
- [ ] Spacing uses `SPACING_XS/SM/MD/LG`, not a fresh integer.
- [ ] The new factory's docstring says which role it plays and what it is *not*.
- [ ] Checked in light and dark, on a small and a large panel.
- [ ] The one-line-primary-color test still holds.
