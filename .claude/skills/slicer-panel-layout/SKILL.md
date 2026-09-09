---
name: slicer-panel-layout
description: Change what a tool panel shows and how it is arranged - which fields appear, their labels, sections, tabs or grids, sliders, conditional visibility, a custom widget. Use when a panel is crowded or unusable, a field is missing, wrongly labelled or should be hidden from clinicians, or a module needs something the generated form cannot express.
---

# Laying out a tool panel

A panel is **generated from the tool's schema**. `formgen.build()` turns
`GET /tools` into widgets and `ServerToolWidgetBase` assembles them; no module
declares a field, a label or a section. So the first question is never "which
widget do I add" - it is **which of three repositories owns this change**.

## 1. Where does this change belong?

| The complaint | Owner | What you edit |
|---|---|---|
| Wrong wording, wrong box, wrong order, too many options visible at once, a knob a clinician should never see | the **tool**, in `SADT-VISOR` | that tool's `layout.py` (presentation hints), merged into its schema by `describe.py` |
| An argument exists or does not exist, its type, its bounds | the **tool's `run()` signature**, in `SADT-VISOR` | the signature; the schema is generated from it |
| A hint is written but never arrives in Slicer | the **server**, in `VISOR-serve` | the schema vocabulary - a key the server does not name is dropped in transit |
| A *kind* of widget does not exist yet (a new `ui:` value, a new type) | **this repo** | `formgen.py`, plus a stub test |
| One module needs something no schema can express | **this repo** | a hook on the module's widget, see §4 |

Rows 1 and 2 are the common case. Reaching for `formgen.py` because ASO's panel
is crowded is the mistake this architecture exists to prevent: the old modules
put the anatomy inside a hand-written `QStackedWidget`, and that is what the
generated panel replaced.

## 2. The presentation vocabulary

Published verbatim by `GET /tools`, ignored by the server's `validate()`, all
optional. A tool declaring none renders exactly as it did before they existed -
asserted for `example_tool` in `test_formgen.py`, and that guarantee is not
negotiable.

| Field | Read by | Effect |
|---|---|---|
| `label` | `formgen.label_for` | the text beside the widget. Absent: the argument name prettified |
| `section` | `formgen.section_of` | which `ctkCollapsibleButton` the row lands in. Absent: `formgen.DEFAULT_SECTION` (`"Inputs"`). Boxes appear in the order the schema first mentions them; `"Outputs"` already exists and holds the output folder picker |
| `visible_when` | `formgen.is_visible` | `{other_arg: value}` (a list means any-of); all entries must match, or the row is hidden, label included |
| `options_when` | `formgen.allowed_options` | `{other_arg: {value: [option, ...]}}` - narrows a choice's own options instead of hiding the field |
| `hidden` | `formgen.is_visible` | never rendered. For `device`, `tile_step_size`, worker counts: the tool keeps its default, the deployment keeps the knob |
| `ui` | `formgen.MultiChoiceGroup`, `_make_numeric_widget`, `_make_vec2_widget` | `"tabs"`/`"grid"`/`"inline"` on a multichoice, `"slider"` on a bounded number, `"joystick"` on a `vec2` |
| `groups` | `formgen.MultiChoiceGroup` | `{group name: [option, ...]}` for the two grouped layouts |

Choosing between multichoice layouts:

- **`"tabs"`** - a catalog too long to scroll in one piece (ALI's 119 landmarks).
- **`"grid"`** - options whose *position* carries meaning. ASO asks for teeth
  "spread across the arch"; a column of 32 check boxes cannot show whether a
  selection is spread or clustered. It scrolls horizontally rather than
  wrapping, because wrapping an arch onto two lines destroys the adjacency the
  layout exists to show.
- **`"inline"`** - a handful of short options.
- Anything unknown falls back to the flat column **with a logged warning**. Keep
  that: a hint from a newer server must never break an older client.

## 3. Invariants a layout change must not break

- **Every layout reads back identically.** `MultiChoiceGroup.boxes` is keyed and
  ordered by `choices` whatever the layout, so `collect()` and the JSON the
  client builds cannot tell them apart. A wrong layout is ugly; it is never
  wrong on the wire. Pinned by
  `MultiChoiceLayoutTest.test_every_layout_reads_back_identically`.
- **An option no group mentions is rendered in a trailing `"Other"` group**, not
  dropped - dropping it would hide a choice the tool genuinely offers.
- **`visible_when` is presentation, not validation.** A hidden row is not sent,
  so the tool's declared default applies. Never encode a rule there that the
  tool needs for correctness; a direct API call bypasses the panel entirely.
- **Bounds alone never switch the widget kind.** `min`/`max` constrain the spin
  box; only `ui: "slider"` makes a slider. A bound added server-side for
  validation must not silently change the interface.
- **An untouched field still travels.** `collect()` sends every widget, so a
  scalar's starting value comes from the schema's `initial` - not from Qt's own
  zero, which once sent `0` where the tool's default was wanted.

## 4. Escape hatches, in the order to try them

1. **A presentation hint** in the tool's `layout.py`. Almost always this.
2. **`configureFields()`** - touch up the generated widgets once they all exist:
   a placeholder, a connection between two fields. Called at the end of *every*
   panel build.
3. **`addExtraWidgets(layout)`** - a button or field of the module's own, added
   after the generated form and before Apply/Cancel. Called only on the **first**
   build.
4. **`FILE_INPUTS` / `RESULT_KIND`** - overrides for what the schema cannot say:
   a `"volume_node"` picker, a forced picker kind, `"none"` to drop an optional
   file argument; and whether a returned file is loaded into the scene or saved.
   Modes: `auto`, `single_file`, `folder_zip`, `file_or_folder`, `volume_node`,
   `none`. Result kinds: `text`, `segmentation`, `labelmap`, `volume`, `model`,
   `save_as`.
5. **`AUTO_UI = False` + `buildCustomUI(layout)`** - last resort, and it forfeits
   every change the server can make without a client release. Write down why.

**The rebuild trap.** The panel is thrown away and rebuilt from scratch when a
server that was down at `setup()` comes back, and on reload. Anything applied
outside `configureFields()` is lost on that rebuild, leaving a panel subtly
different from the one the module describes. If a tweak has to survive, it goes
in `configureFields()`.

Keep the module thin: `ExampleTool.py` is 41 lines, `AMASSS.py` 143. A widget
file growing past ~250 lines usually means knowledge that belongs in the tool's
`layout.py` has leaked into the client.

## 5. Verify

```bash
cd ServerToolsCore/Testing/Python && python3 -m unittest test_formgen test_runs
```

`test_formgen.py` is driven by `EXAMPLE_TOOL_SCHEMA`, the real `GET /tools`
entry for `example_tool`, verbatim. A new hint or widget kind gets a case there:
which widget the schema produces, in what order, with what initial state, and
what it reads back as.

If the change is server-side, check what actually arrives before touching the
client - a hint the server drops looks exactly like a client that ignores it:

```bash
curl -s http://localhost:8001/tools | python3 -m json.tool | less
```

Then reload the module in Slicer. The panel refetches `GET /tools` on reload, so
a schema change shows up without restarting Slicer.
