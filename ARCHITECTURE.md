# Architecture: client/server tool infrastructure

This describes `ServerToolsCore`, the shared infrastructure that lets a Slicer
module become a thin GUI over a tool exposed by the remote tool server, and
`SurgMovPred`, the first module rebuilt on top of it. Read this before adding
or touching either.

## Goal

Move computation off the Slicer interpreter and onto a server. Slicer modules
stop containing business logic, HTTP calls, threading, or CSS; they declare
*what* tool they call and *how* its input/output map onto the Slicer scene,
and inherit everything else.

## Directory layout

```
SlicerAutomatedDentalTools/
├── CMakeLists.txt                          # add_subdirectory for each module below
├── ARCHITECTURE.md                         # this file
├── ServerToolsCore/                        # hidden scripted module, no GUI
│   ├── CMakeLists.txt
│   ├── ServerToolsCore.py                  # ScriptedLoadableModule shell, parent.hidden = True
│   │                                        # (also applies saved settings on Slicer startup)
│   ├── Testing/Python/test_client.py       # plain unittest, requests mocked, no Slicer needed
│   ├── Testing/Python/test_transfer.py     # chunked upload / ranged download, against a real socket
│   ├── Testing/Python/test_formgen.py      # plain unittest, qt/ctk/slicer stubbed, no Slicer needed
│   ├── Testing/Python/test_joystick.py     # the 2D pad's value/geometry logic, same stubs
│   ├── Testing/Python/test_hosted_test_files.py  # picking a tool's hosted test file: the
│   │                                        # download, the cache, the unpacking, the scene
│   ├── Testing/Python/qt_stubs.py          # the stand-ins test_formgen/test_joystick run against
│   └── ServerToolsCoreLib/                 # the importable Python package
│       ├── __init__.py                     # get_client() + ToolServerClient/ToolResult/ServerToolError
│       ├── config.py                       # SERVER_URL, API_TOKEN, VERIFY_TLS, TIMEOUT, TRANSFER_* (compiled-in defaults)
│       ├── client.py                       # ToolServerClient — the only class that speaks HTTP
│       ├── transfer.py                     # parallel chunked upload / ranged download of big files
│       ├── errors.py                       # ServerToolError + HTTP status → message mapping
│       ├── slicer_io.py                    # TempWorkspace, node export, zip/unzip, result loading
│       ├── design.py                       # theme tokens, dark/light detection, styled-widget factories
│       ├── formgen.py                      # /tools schema → Qt widgets, and back
│       ├── joystick.py                     # JoystickPad, the 2D pad behind vec2's ui:"joystick"
│       ├── worker.py                       # off-UI-thread execution (BackgroundJob)
│       ├── base_widget.py                  # ServerToolWidgetBase: all the Slicer boilerplate
│       └── settings_qt.py                  # QSettings-backed override of config.py's defaults
├── ServerToolsSettings/                    # visible module: edit server URL/API key/TLS/timeout
│   ├── CMakeLists.txt
│   └── ServerToolsSettings.py
├── SlicerCloud/                            # visible module: deploy & maintain the server itself
│   ├── CMakeLists.txt
│   ├── SlicerCloud.py                      # the panel (install / update / stop, tool-data picker)
│   ├── Testing/Python/test_deploy.py       # plain unittest, real subprocesses, no Slicer needed
│   └── SlicerCloudLib/
│       ├── __init__.py
│       └── deploy.py                       # clone + drive scripts/server_ctl.py. No slicer, no qt.
├── SurgMovPred/
│   ├── CMakeLists.txt
│   └── SurgMovPred.py                      # ~35 lines, declarative
├── ExampleTool/                            # reference client for the server's example_tool
│   ├── CMakeLists.txt
│   └── ExampleTool.py                      # ~35 lines, declarative
├── ALI/                                    # automatic landmark identification
│   ├── CMakeLists.txt
│   ├── ALI.py                              # declarative, plus a run-report summary
│   ├── Testing/Python/test_ali_client.py   # ALI's schema as a fixture, qt/ctk/slicer stubbed
│   └── ALI_Method/                         # former local module, left in place but unwired
├── ASO/                                    # automated standardized orientation
│   ├── CMakeLists.txt
│   ├── ASO.py                              # declarative, plus optional result loading
│   ├── Testing/Python/test_aso_client.py   # ASO's schema as a fixture, same stubs
│   └── ASO_Method/                         # former local module, left in place but unwired
├── AREG/                                   # registration of two timepoints
│   ├── CMakeLists.txt
│   ├── AREG.py                             # declarative, plus optional result loading
│   ├── Testing/Python/test_areg_client.py  # AREG's schema as a fixture, same stubs
│   └── AREG_Method/                        # former local module, left in place but unwired
├── SurgMovPred_CLI/                        # left in place but unwired (see "SurgMovPred_CLI" below)
└── ALI_CBCT/, ALI_IOS/, ASO_CBCT/, ASO_IOS/, AREG_CBCT/, AREG_IOS/, AREG_IOSCBCT/
                                            # the CLIs they used to drive, likewise unwired
```

### Deviation from a literal reading of the brief

The task brief sketched the tree with everything nested under a top-level
`SlicerServerTools/` wrapper folder. That was a diagram, not a literal
instruction — every existing module in this repository (`AMASSS`, `ALI`,
`SurgMovPred`, ...) lives flat at the repository root and is registered
directly from the root `CMakeLists.txt`. `ServerToolsCore/` and `SurgMovPred/`
follow that convention instead of introducing a new nesting level nothing else
in the repo uses.

## Dependency rule — enforced, not just documented

> `client.py`, `transfer.py` and `errors.py` import neither `slicer` nor `qt`.
> `base_widget.py`, `formgen.py`, `design.py`, `slicer_io.py`, `worker.py`
> import neither `requests` nor anything HTTP.

`ServerToolsCoreLib/__init__.py` only imports `client`, `errors` and `config`
— none of which touch `slicer`/`qt`/`ctk`. That is what makes
`import ServerToolsCoreLib` (and therefore `client.py`) work in plain CI with
no Slicer installed: see `ServerToolsCore/Testing/Python/test_client.py`,
which mocks `requests` and runs with `python3 -m unittest`.

The GUI-facing modules (`design`, `formgen`, `slicer_io`, `worker`,
`base_widget`) are imported explicitly and only by code that already runs
inside Slicer (`base_widget.py` does `from . import get_client` lazily inside
`__init__`, precisely so importing `base_widget` doesn't require pulling in
`requests` at class-definition time either way — though in practice it will,
since `client.py` has no Slicer dependency to avoid).

## Tests

Plain-unittest suites, all registered as ctests and all runnable with
`python3 -m unittest` from their own `Testing/Python/` folder — no Slicer
interpreter launch:

- **`test_client.py`**, `requests` mocked (on `requests.Session`, since every
  call now goes through one pooled session). HTTP behavior, local schema
  validation, the request/response shape (including the whole `example_tool`
  round-trip: what each argument type looks like as a form field), error
  mapping, result filenames, and which transfer path `run()` picks for a given
  input size.
- **`test_transfer.py`**, the one suite that is *not* mocked: it runs the real
  chunked upload and ranged download against a real `ThreadingHTTPServer` on
  a real socket. What that module does is concurrency and byte offsets, and a
  mock proves nothing about either. The fake server is deliberately hostile -
  it drops parts, corrupts checksums, cuts ranges short and refuses ranges
  outright, because every one of those is something a real remote server does.
- **`test_formgen.py`** — `qt`/`ctk`/`slicer` replaced by the small stand-ins
  in `qt_stubs.py`. Which widgets a schema produces, in which order, with which
  initial state, and what they read back as. That is pure Python once the
  widget classes are stubbed; it obviously does not test Qt itself, only the
  schema-to-widget logic, which is where the tool contract actually lives.
  It runs against `EXAMPLE_TOOL_SCHEMA`, the server's real `GET /tools` entry
  for `example_tool` copied verbatim.
- **`SlicerCloud/Testing/Python/test_deploy.py`** — nothing mocked at all.
  A stand-in `scripts/server_ctl.py` is written to a temp directory and really
  executed, because the two things worth testing there are the pipe protocol
  (one JSON result on stdout *while* a live log streams on stderr) and the
  deadlock a naive two-pipe read walks into — neither of which a mocked
  `Popen` can exhibit. One test writes 20 000 stderr lines specifically to
  push past the pipe buffer.
- **`test_joystick.py`**: the 2D pad under the same stubs. The value/pixel
  mapping, clamping, and the gesture handlers' arithmetic (absolute and
  spring-back drags, wheel, arrows). Painting is not exercised, there is no
  real Qt to paint with.
- **`test_hosted_test_files.py`** — what happens when a user picks one of a
  tool's server-hosted test files: the download, the session cache, a hosted
  folder unpacked, and what does and does not reach the MRML scene. Same
  stubs plus the handful of `slicer.util` functions that path touches, and a
  `BackgroundJob` stand-in that runs the task on a **real** worker thread and
  delivers it when the test says so — because "this must never block the
  Slicer window" is the property under test, and a synchronous fake could not
  show it.

## How the pieces fit together

```
                       ┌─────────────────────┐
                       │   Tool server (HTTP) │
                       └──────────┬───────────┘
                                  │ requests
                            ┌─────▼─────┐
                            │ client.py │  ToolServerClient, ToolResult, ServerToolError
                            └─────┬─────┘
                                  │ get_client() singleton
                    ┌─────────────▼──────────────┐
                    │      base_widget.py         │  ServerToolWidgetBase
                    │  (Slicer lifecycle, apply/  │
                    │   cancel, error handling)   │
                    └──┬──────────┬─────────┬─────┘
                       │          │         │
                 formgen.py  slicer_io.py  worker.py
              (schema→Qt)   (MRML bridge)  (background thread + QTimer drain)
                       │
                  design.py (theme/colors, used by all of the above)
                       │
              ┌────────▼────────┐
              │ SurgMovPredWidget│  TOOL_NAME / FILE_INPUTS / RESULT_KIND + optional hook overrides
              └──────────────────┘
```

## `client.py`

- `ToolServerClient(server_url, token, verify_tls=True, timeout=600)`.
- `health()` → bool, never raises (a failed health check just means "offline").
  Uses a short fixed timeout (`_HEALTH_CHECK_TIMEOUT = 10`s), not the tool
  timeout — it feeds the status banner on every `enter()` and must not be able
  to block the UI for up to 600s. Only `(requests.RequestException, ValueError)`
  are swallowed into `False`; a programming error (e.g. an unexpected response
  shape raising `AttributeError`) is not silently hidden.
- `list_tools(force_refresh=False)` → `{tool_name: schema}`, cached on the
  instance after the first call. `get_client()` in `__init__.py` returns a
  singleton so the whole extension shares one cache — the first module opened
  pays for `GET /tools`, the rest are free.
- `get_tool_schema(tool_name, force_refresh=False)` looks the tool up in the
  cache (fetching if needed) and raises `ServerToolError` listing the available
  tool names if it doesn't exist (e.g. `"Unknown tool 'x'. Available: a, b,
  c"`). `force_refresh` re-fetches instead of trusting the cache — used when a
  panel retries after a failure, where the cached list may be the very reason
  the tool wasn't found.
- `is_file_type(type_name)` — `type_name in ("file", "path") or
  type_name.endswith("_file")`. Every place in the codebase that needs to know
  "is this schema argument a file upload" (`client.py`, `formgen.py`,
  `base_widget.py`) goes through this one function instead of comparing against
  a literal, so a new file-ish type needs no client-side change. Exported from
  `ServerToolsCoreLib/__init__.py` alongside `get_client()`.

  **`"path"` is the one that broke the rule, and it is now the normal case.**
  A *packaged* tool — every clinical tool, since they moved to the `SADT-VISOR`
  repository — declares exactly one file type, `"path"`, for every file or
  folder it takes: its schema is generated from a `run(scans: Path, ...)`
  signature, and a Python annotation cannot say more than "a path". It ends in
  neither `"file"` nor `"_file"`, so before it was listed here such a tool's
  schema reported **no file arguments at all** and the panel refused to build:

      FILE_INPUTS declares ['scans'] but the server's 'AMASSS' schema
      doesn't have them as file arguments (it has: []).

  It is listed rather than pattern-matched because it is one name, fixed by the
  tool contract; guessing at "anything not obviously scalar" would turn every
  unknown type into a file dialog.

  The `"..._file"` vocabulary below (`nifti_file`, `zip_file`,
  `volume_or_zip_file`, …) belongs to the server's older in-process `ArgSpec`
  path, which now carries only two demonstration tools. It is still read
  because the client must serve both, and because nothing in the client should
  need to know which kind of tool it is talking to.
- **Schema-reading helpers**, next to `is_file_type` and exported the same way
  (no Qt, no HTTP — just "how do I read a schema argument", which keeps them
  unit-testable in plain CI):
  - `argument_types(spec)` — every type an argument accepts. The server sends
    both a single `type` and the full `types` list; an argument accepting
    several (`example_tool`'s `input`: `["csv_file", "folder"]`) is only
    described by the latter. Falls back to `[type]` for an older schema.
  - `accepts_folder(spec)` — whether `"folder"` is among them, i.e. whether the
    user may pick a whole directory (zipped client-side before upload; HTTP has
    no notion of a folder). The server detects a `"folder"`-typed argument,
    extracts the archive, and strips a lone root directory — so it makes no
    difference whether the zip holds `cohort/a.csv` or `a.csv`.
  - `file_extensions_for(spec)` — the extensions a file picker should offer:
    `["csv_file", "folder"]` → `(".csv",)`. **Read from the schema**, which
    publishes `extensions` (`{type name: [extension, ...]}`, the server's own
    `FILE_TYPES` table) alongside `types`; only the *file* types count, since
    `"folder"`'s `.zip` says what a zipped folder may be uploaded as, not what
    a picker should show. An empty result means "don't restrict" (the generic
    `"file"` type, or a folder-only argument).

    A type name does not reliably spell out its extensions — `nifti_file` is
    `.nii`/`.nii.gz`, `volume_or_zip_file` is seven of them — so the client
    used to keep a copy of that table, and it drifted: `volume_or_zip_file`
    (the server's AMASSS input) was missing from it and derived as
    `.volume_or_zip`, a file dialog matching nothing. The table survives only
    as a fallback for a server predating the `extensions` field, together with
    the `"<x>_file"` → `".<x>"` convention; **a new file type is added
    server-side, never here**. A compound name that fallback cannot read
    (underscores left once `_file` is stripped) yields no filter rather than a
    nonsensical one.

    For a **packaged** tool the fallback can say nothing at all — `"path"`
    carries no extension in its name — so `extensions` is not an optimisation
    there, it is the only source. A tool that does not publish it gets a picker
    filtered by the server's global `ALLOWED_EXTENSIONS`, which accepts a
    `.nii.gz` but will not offer it; that is a gap to fix in the tool's
    `layout.py`, not here.
- `list_tool_data(tool_name)` → `{"models": [...], "testfiles": [...],
  "entries": {...}}` — what the server hosts for this tool
  (`GET /tools/{tool}/data`, Bearer-protected unlike `/tools`). Backs the
  server-selectable dropdowns (see `formgen.py` / `base_widget.py` below). Not
  cached: fetched once per module `setup()`, since the server-side list can
  change independently of the `/tools` schema. Uses `_TOOLS_FETCH_TIMEOUT`,
  same rationale as the schema fetch.

  `entries` is the richer half a later server added: the same names with a
  `kind` (`"file"`/`"folder"`) and a `size` in bytes, so the picker can say
  what a click is about to cost. `client.testfile_entries(data)` normalises
  both shapes into `[{"name", "kind", "size"}]` — an older server publishing
  names only yields `kind`/`size` of `None`, and a backend that cannot size a
  tree cheaply sends `null` for one entry. **Absent is never zero**: unknown
  renders as nothing rather than as `0 B`. The flat `testfiles` list stays
  authoritative for which names exist, since it is the field every server
  sends.
- `download_testfile(tool_name, filename, destination, progress_cb=None)` —
  one of the tool's hosted test files, from
  `GET /tools/{tool}/testfiles/{name}` with the Bearer token. A hosted
  *folder* arrives as a `.zip` the server builds; unpacking is the caller's
  business. Pulled over parallel byte ranges (`transfer.download_ranged`,
  the same path `_download_reference` takes for a large result) when the
  server advertises `Accept-Ranges`, streamed sequentially otherwise — the
  semi-automated CBCT set is 648 MB, and one connection is bound by its own
  congestion window long before it is bound by the link. **Models are
  deliberately not fetchable**: the server declines to stream one, weights
  being selected by name and used in place.
- `run(tool_name, args=None, files=None, output_dir=None, progress_cb=None)`
  → `ToolResult(kind="text"|"file", text=..., path=...)`. `files` is
  `{schema_argument_name: local_file_path}` — **there is no single reserved
  "file" key**: a tool can declare several independent file-type (per
  `is_file_type`) arguments (the real `surg_mov_pred` schema has both
  `"model": {"type": "zip_file", ...}` and `"input": {"type": "zip_file",
  ...}`), each uploaded as its own multipart field named after its schema
  argument. A tool with one file argument just passes a one-entry dict. It:
  1. validates `args`/`files` against the cached schema locally
     (`_validate_against_schema`) — unexpected/missing scalar arguments, an
     unexpected file argument name, a file argument name whose schema type
     isn't file-like, and a missing *required* file argument are all caught
     before a network round-trip (an optional file argument doesn't force an
     entry in `files`);
  2. stringifies every scalar (`bool` → `"true"`/`"false"`) since the server
     does the coercion. A `dict` — how a `"multichoice"` argument arrives, see
     `formgen` below — is sent as `json.dumps(...)`, e.g.
     `outputs={"summary": true, "preview": false, "columns": true}`. **Two
     things there are load-bearing.** *The whole dict travels, unchecked
     options included*: server-side, what is sent **is** the selection — an
     option left out counts as unchecked whatever its declared default, and
     omitting the argument entirely is what applies the defaults. So "every box
     unchecked" and "argument absent" are different requests, and only the real
     state of the boxes tells them apart. *And it is JSON, not the `a,b`
     shortcut*: the server also accepts a comma-separated list of the checked
     options, but that spelling is for `curl` — it breaks the moment an option
     name contains a comma. A `"choice"` argument is a plain string (the
     selected option's name) and needs none of this;
  3. sends anything big enough to be worth it (`>= 2 x TRANSFER_CHUNK_MB`)
     **ahead of this request**, in parallel parts, via `transfer.upload_file`
    , see `transfer.py` below for why. Those arguments then travel as a
     `{argument name: upload id}` map in the reserved `__uploads__` form field
     instead of as bytes. A server without the endpoints raises
     `UnsupportedByServer`, the verdict is cached on the client, and everything
     reverts to the single-request path below;
  4. opens every *remaining* file in `files` in a loop, all closed in one
     `finally` so a handle is never leaked even if a later one fails to open;
     each is sent as `files_payload[arg_name] = (basename, handle)`, the
     filename (with extension) has to travel with the upload since the server
     validates extensions from it; POSTs multipart form-data with the
     `Authorization: Bearer` header and `X-Result-Delivery: reference`;
  5. converts every `requests.RequestException` into `ServerToolError`, no
     `requests` exception is allowed to reach the GUI;
  6. dispatches on `Content-Type`. `application/json` is a text result, unless
     it carries a `result_ref`, which is a *file* result the server has kept for
     us: `_download_reference` then pulls it over parallel `Range` requests
     (`transfer.download_ranged`) and releases it with `DELETE /results/{id}`.
     The reference's `filename` is run through `os.path.basename`, since the
     name is the server's to choose and a path separator in it would place the
     result outside `output_dir`. Anything else is a streamed body,
     written to `output_dir` under a filename resolved by
     `_result_filename`: the response's `Content-Disposition` header if
     present (the real filename, e.g. `predictions_outputs.xlsx`); otherwise
     `.nii.gz` when the tool's schema declares `output_kind == "segmentation"`
     (so `slicer.util.loadSegmentation`, which picks its reader from the
     extension, doesn't choke on a bare `.bin`/`.gz`); otherwise
     `mimetypes.guess_extension(Content-Type)` (mirrors the server's own
     `mimetypes.guess_type()`), falling back to `.gz`/`.bin` only if that
     lookup fails. Getting a real extension here matters beyond cosmetics: see
     `slicer_io.is_extractable_archive` below, which decides whether to unpack
     a "save_as" result purely from this filename's extension.

  **The POST is sent with `stream=True` and the body is written to disk in 1 MB
  chunks** (`_DOWNLOAD_CHUNK_BYTES`), never through `response.content`. A result
  archive is routinely hundreds of MB — one segmentation plus one surface per
  structure per scan — and buffering that whole body in Slicer's RAM before the
  first byte reaches disk scales exactly as badly as it sounds. Streaming also
  changes what `timeout` means for the download: it becomes the gap allowed
  *between* chunks rather than a budget for the whole transfer, so a large but
  flowing response can no longer time out merely for being large.

  **A file result is then verified before it is accepted** (`_verify_download`):
  the bytes written are compared against `Content-Length`, and a `.zip` has
  every member CRC-checked with `zipfile.testzip()`. On either failure the
  partial file is **deleted** and a `ServerToolError` raised. This is not
  belt-and-braces: a connection dropped mid-body leaves a truncated archive
  whose surviving central directory still unpacks, so `_handleSaveAsResult`
  would silently deliver a *subset* of a patient's segmentations — a wrong
  result that looks like a right one, the worst failure mode there is. The
  length check is skipped when `Content-Encoding` is set (see
  `_expected_length`): `Content-Length` then counts wire bytes while
  `iter_content` yields the decompressed stream, so the two differ legitimately.

  **`progress_cb` is called throughout**, not only around the request. The
  upload used to be a single opaque `requests.post` that reported nothing at all
  until the server answered, minutes of a panel saying `Sending '<tool>'
  request...` while a 100 MB scan went up, and the chunked path fixes that at
  the source: every part that lands moves a counter, so the panel shows
  `"Uploading scan.nii.gz... 48.2 / 105.0 MB (46%) at 11.3 MB/s, 5s left"`.
  Downloads read the same way (`"Downloading results... 8.2 / 14.1 MB (58%)"`,
  the total omitted whenever `_expected_length` returns `None`). See "Telling the user something is
  happening" below for why silence here is a bug and not merely unpolished.
- `errors.error_for_status(status_code, server_message)` maps 401/404/422/400/
  413/500 to a `ServerToolError`. The server's `detail` is shown **verbatim**
  whenever there is one; the strings in that function are fallbacks for a
  response with no usable body. `_server_message` reads JSON `detail`/`message`
  first, then falls back to the raw response body (truncated to 500 chars) so a
  plain-text error response isn't dropped. This matters most for the argument
  errors, where the server's message is the only one that can be specific:
  - **422** — missing / wrong-typed argument, or an option outside a
    `choice`/`multichoice` list: `Argument 'preview_format': unknown option
    'xml'. Expected one of: csv, json`. Nothing client-side second-guesses that
    or falls back to some other value — an out-of-list value is simply shown as
    the server explains it.
  - **400** — wrong extension or invalid archive: `Unsupported file extension
    for 'input'. Allowed: ('.csv', '.zip')`.
  - **413** — the detail names the actual limit (`File exceeds the 500 MB
    limit.`), which the client has no other way of knowing.
  - **401** — missing or wrong token.

  500 is the one exception: a crash inside a tool is not a message meant for
  the user, and its detail may leak server-side internals, so it keeps a
  generic wording.
- `configure(server_url=None, token=None, verify_tls=None, timeout=None)` /
  read-only properties `server_url`/`token`/`verify_tls`/`timeout` — updates
  the already-constructed singleton **in place** (only the fields passed) and
  unconditionally drops the cached `/tools` schema, since it may no longer
  belong to the newly-configured server. This is what lets
  `ServerToolsSettings` change the server URL/API key at runtime without a
  Slicer restart — see the dedicated section below.

## `base_widget.py` — `ServerToolWidgetBase`

Owns the entire Slicer lifecycle (`setup`/`cleanup`/`enter`/`exit`, scene
observers), the schema-driven GUI, the theme, the server status banner
(`GET /health` refreshed in `enter()`, on a background thread), the Apply/
Cancel buttons and their enabled state, the async call via `worker.py`, error
display, and temp-file cleanup (`try/finally` around a `TempWorkspace`, and
also on `cleanup()` in case the module is closed mid-request).

A subclass declares:

**`TOOL_NAME` is the only required attribute.** Everything the tool's schema
already states is derived from it; `FILE_INPUTS` and `RESULT_KIND` are
**overrides**, for the few things a server cannot know. A module whose schema
answers every question is one line:

```python
class ExampleToolWidget(ServerToolWidgetBase):
    TOOL_NAME = "example_tool"
```

and one that needs to say more says only that much:

```python
class SurgMovPredWidget(ServerToolWidgetBase):
    TOOL_NAME   = "SurgMovPred"
    FILE_INPUTS = {"input": "folder_zip"}   # schema says zip_file; we want a folder picker
    RESULT_KIND = "save_as"                 # output_kind "file" doesn't say what to do with it
    AUTO_UI     = True                      # False → override buildCustomUI()
```

What is derived, and what a module still has to say:

| Question | Answered by the schema? |
|---|---|
| which arguments are file inputs | **yes** — every argument where `is_file_type(type)` |
| file picker, folder picker, or both, and with which extensions | **yes** — from `types` (`formgen.auto_file_mode`) |
| `output_kind` `text` / `segmentation` / `files` → `RESULT_KIND` | **yes** — `text` / `segmentation` / `save_as` |
| `output_kind` `file` → load as a volume? a mesh? just save it? | **no** — MRML knowledge, defaults to `save_as` |
| fill an input from a node in the scene (`volume_node`) | **no** — the server doesn't know a scene exists |
| offer a folder picker for an argument the server types as a plain zip | **no** — an ergonomics choice (`SurgMovPred`) |
| leave an optional file argument out of the panel (`"none"`) | **no** — a module's decision |
| offer the scene's open volumes in the input dropdown | **yes**: any file argument `formgen.accepts_volume` says yes to (volume-ish type name or a published volume extension) |
| offer the tool's test data on an input row | **yes**: `server_selectable: "testfile"`, listed by `GET /tools/{tool}/data`. No module declares anything — `TEST_DATA` is gone |

This is what keeps "add a field to a tool = zero client-side lines" true for
*file* arguments too, not just scalar ones: a new file argument server-side
appears in the panel with the right picker, unannounced.

### Several runs from one panel: `_Run`, and the admission limit

A panel used to hold one `_job`, and Apply hid itself behind Cancel until it
finished. That is the wrong shape for the wait a clinician actually has, which
is a **cohort**: the upload of the next patient has no reason to sit behind the
inference of the previous one. `onApplyButton` now appends a `_Run` — its own
inputs, its own `TempWorkspace`, its own thread — and `_pumpRuns` starts as many
as `config.CONCURRENT_RUNS` allows. Apply stays available; clicking it again
queues.

**The default is one run at a time per tool**, which is the shape a panel is
expected to have — a panel running two of the same tool at once is not what a
panel looks like. What changed against the old behaviour is only that a second
Apply *waits its turn* instead of being refused.

**Different tools are unaffected, and that needs no setting.** Each panel holds
its own `_runs`, so AMASSS and ALI run side by side; the cap that applies there
is the server's `MAX_CONCURRENT_TOOLS`, not anything on this side. Measured from
one Slicer session: four runs started together, `3.7x overlap`.

**One mechanism serves both shapes, and the only thing between them is the
number.** Raising `CONCURRENT_RUNS` lets N runs of one tool be in flight, which
is worth doing for a cohort — the transfer of the next patient overlaps the
inference of the current one. Keep it well under 4: the server admits
`MAX_CONCURRENT_TOOLS = 4` **in total** across every panel and every client, so
spending them all from one panel would make another module's run queue behind
this one's cohort.

**It is not a speed multiplier, and the docstrings say so.** The server
serialises the card (`MAX_CONCURRENT_GPU_JOBS = 1`), so four segmentations still
segment one at a time. What overlapping buys is the transfer of the next patient
during the inference of the current one — on a cohort of 94 MB CBCTs, most of
the wall clock — plus genuine parallelism across tools that do not both want the
GPU.

Four properties are worth more than the mechanism, and each has a test that
fails without it (`test_runs.py`, verified by mutation):

- **The inputs are read at Apply time, never at start time.** A run that starts
  three minutes later because two were ahead of it must not pick up whatever the
  pickers hold by then — that would run patient 3's scan under patient 1's
  request, successfully, and say nothing.
- **One scratch directory per run.** Two runs sharing one temp folder would
  overwrite each other's inputs; finishing a run closes its own and no other.
- **A failure costs one run.** The rest of the cohort goes on, and the queue
  advances past it.
- **Each callback binds its own run** (`lambda ..., run=run:`). Without that,
  every progress line would report against whichever run was queued last.

The progress label carries one line per run, and **a single run reads exactly as
it always did** — no run number, no label. The prefix appears only once there is
something to tell apart, which is the overwhelmingly common case left alone and
nine modules' worth of habit preserved. A queued run says `queued`; the Cancel
button says `Cancel all` above one, and cancels the queue with it.

### Sections and conditional fields

`_buildAutoUI` creates one `ctkCollapsibleButton` per `section` the schema
names, in first-mention order, and routes every row — scalar fields *and* file
inputs — to the box its own spec names. `DEFAULT_SECTION` ("Inputs") is always
created even when every argument claims another one, since it is where anything
sectionless goes, including the error path's empty schema; an unused box holds
no rows and is hidden, so it costs nothing on screen. `"Outputs"` is created
whenever `resultKind == "save_as"` for the output-folder picker, and a tool may
also put arguments of its own in it (ASO's `output_suffix` does).

`_wireVisibility` then connects `_applyVisibility` to every argument some other
argument's `visible_when` names, and runs it once so the panel *opens* filtered
rather than showing everything until a combo box is touched. It is called from
`_buildAutoUI` and not from `setup()`, for the same reason `configureFields()`
exists: the panel is rebuilt from scratch when a server that was down comes
back, and anything wired outside the build is silently lost on that rebuild.

`_applyVisibility` hides each failing row's label and field together, records
the set in `_hiddenArgs`, and **hides a whole section once every row it owns is
hidden**. That last rule is what turns two mutually exclusive sets of arguments
into the old module's two stacked pages, with no client-side notion of a "page"
anywhere. A section holding a row no argument owns (the output folder) is
listed in `_sectionsWithOwnRows` and never hidden.

Four call sites then read `_hiddenArgs`, and each one is load-bearing:

- **`collectArgs`** drops hidden arguments entirely. Not cosmetic: a
  `multichoice` reads back as the *complete* `{option: checked}` dict and the
  server reads what it receives **as** the selection, so sending ASO's
  `ios_teeth` along with a CBCT run states a selection the user was never shown
   — and freezes it at whatever the invisible widget was built with, even after
  someone changes the default server-side. Dropping it is what lets the
  server's own default apply.
- **`_prepareOneInputFile`** uploads nothing for a hidden file argument, and
  **`_serverSideSelections`** sends no hosted name for one.
- **`all_required_filled` / `_inputReady`** skip them, so a hidden required
  field cannot disable Apply forever with nothing on screen to explain why. No
  tool declares one today; this is what keeps that from becoming a dead-locked
  panel if one does.

**Apply also ignores an OPTIONAL file argument, whether or not it is visible.**
`all_required_filled` had always skipped `required: false` scalars, and
`_inputReady` did not do the same for file rows — so an optional file input
disabled Apply until something was picked for it, with nothing on the panel
saying that empty field was what Apply was waiting for. It went unnoticed
because every file argument of every tool was required until AREG: its
`mgl_landmarks` exists only to *reuse* landmarks the server would otherwise
predict, so requiring it made the ordinary run the one you could not launch.

**And `_prepareOneInputFile` uploads nothing for one that is empty.** The two
halves are one rule and were fixed together: letting Apply fire on an empty
optional row is only correct if the upload path then leaves that argument out.
It used to return `widget.currentPath` — the empty string — as though it were a
path, and the next thing to touch it failed with `No such file or directory:
''`, which names nothing a user can act on. Uploading nothing is what makes the
server apply whatever it does when the argument is absent, which for AREG's
`mgl_landmarks` is "predict the landmarks yourself".

The consequence to keep in mind is that a rule like "Semi-Automated CBCT needs
`t1_masks`" is **the server's** — that argument is `required: false` because the
requirement depends on another argument's value, which the schema cannot state.
Apply now fires, and the server answers a 422 naming the field to fill in. That
is the right place for it: the client would otherwise have to re-implement each
tool's cross-argument rules to grey a button out.

`formgen.is_visible` **hides what it cannot evaluate** — a controlling argument
missing from the collected values. That only happens when the schema could not
be fetched (so the panel holds an error, not a form), since the server's
`check_schema` rejects a `visible_when` naming an argument the tool does not
declare. Hiding is the answer that cannot produce a wrong request.

### The panel heals when the server comes back

The panel is built from the schema, so a server that is down when the module
opens leaves nothing but an error label. Nothing used to clear it: the module
stayed broken — still showing `Could not reach the tool server:
HTTPConnectionPool(...)` — for the rest of the Slicer session, against a server
that was back up.

The schema-driven part therefore lives in its own container widget
(`_buildForm`), rebuilt wholesale on either of two signals:

- **the health check turning green** (`_onStatusChecked`), which already runs
  on every `enter()` — coming back to the module is enough;
- **a "Retry" button**, shown under the error only. `enter()` alone would mean
  a user staring at the error has to guess that leaving and coming back fixes
  it.

Both retry with `force_refresh=True`: the cached `/tools` may be exactly what
is wrong (fetched from another server, or before this tool was registered).

Two properties this must keep, both covered by a Slicer-side check:

- **only a *broken* panel is rebuilt** — `_schemaError` gates it. Rebuilding a
  healthy one on every `enter()` would wipe whatever the user had typed;
- **nothing of the failed attempt survives** — replacing the whole container
  rather than clearing a layout takes the error label, the Retry button and any
  stale server-side dropdown with it. The old container is hidden and unparented
  at once but destroyed with `deleteLater()`, since the rebuild can be running
  inside the Retry button's own `clicked` handler.

**The schema is fetched before any widget is built** (`_buildAutoUI`), not
after: a file argument's declared `types` decide what its picker looks like —
file, folder, or the choice between both, and with which extensions — so the
widgets cannot be built without it. Each input's tooltip comes from the same
place, the argument's `description`. The failure path still builds the panel
from an empty schema, with the error shown in it, so the module is never blank.

Overridable hooks (kept deliberately few):

| Hook | Purpose | Default behavior |
|---|---|---|
| `buildCustomUI(layout)` | used when `AUTO_UI = False` | raises `NotImplementedError` |
| `addExtraWidgets(layout)` | add a custom button/field without touching `setup()` | no-op |
| `configureFields()` | touch up the generated widgets (placeholder, initial value, one field driving another) | no-op |
| `collectArgs()` | transform values before sending | `formgen.collect(self._argWidgets)`, minus optional text fields left empty |
| `prepareInputFiles(workspace)` | produce `{schema_arg_name: file_path}` to upload | covers all `FILE_INPUTS` modes already |
| `handleResult(result)` | custom result display | dispatches on `RESULT_KIND` |

`configureFields()` runs at the end of **every** auto-generated build,
`addExtraWidgets()` only at the first. The panel is rebuilt from scratch when a
server that was unreachable at `setup()` time comes back, so anything applied
to a generated widget outside `configureFields()` is silently lost on that
rebuild.

`collectArgs()` dropping an **optional** field left empty is not cosmetic: the
server applies an omitted optional argument's declared default and takes a
present one literally, so `""` asks for an empty value rather than for the
default. That is what turned ALI's untouched `prediction_ID` into
`scan_lm_.mrk.json`. A module wanting to show the default advertises it as a
placeholder (see `ALI.configureFields`), which keeps it written down once,
server-side.

Every file-type argument (per `is_file_type`) the tool's schema declares gets
one row in the "Inputs" section, labeled from the argument name, in schema
order — `formgen.file_input_modes` derives that set, a module never repeats it.
`FILE_INPUTS` is `{schema_argument_name: mode}` **merged on top**, naming only
the arguments whose handling the schema cannot express.

- `"auto"` — **what every argument gets unless overridden**: the picker is
  derived from the argument's declared `types` (`formgen.auto_file_mode`), so
  the module names no type and no extension. The general rule, in one place: an
  argument accepting `"folder"` may be given a whole folder, and one accepting a
  file type as well takes either; the file picker's extensions come from the
  other entries of `types`. It resolves to one of the concrete modes below,
  once, at build time — the answer is needed twice, to build the widget and
  again at upload time to know whether to zip.
- `"none"` — leave an optional file argument out of the panel entirely. The
  only way to *not* offer an argument the schema declares.
- `"single_file"` — a `ctkPathLineEdit` (file mode), name-filtered to the
  argument's extensions; its `currentPath` is sent as-is.
- `"folder_zip"` — a `ctkPathLineEdit` (directory mode); the folder is zipped
  to `<workspace>/<tool>_<arg_name>.zip` via `slicer_io.zip_folder`.
- `"file_or_folder"` — a `formgen.FileOrFolderInput`: **one** path field with a
  "File..." and a "Folder..." browse button. A folder selection is zipped
  exactly like `"folder_zip"`; a file is sent as-is.

  **The user never declares which kind they are providing** — `is_folder()`
  answers from the path itself (`os.path.isdir`), so a mode cannot be set
  wrong. An earlier version had an explicit File/Folder selector; besides the
  extra step, it made wrong requests possible — a folder pasted into a field
  left on "File" was uploaded as a file and died at `open()`.

  It is a plain `QLineEdit` with two buttons rather than a `ctkPathLineEdit`,
  and both halves of that are forced by CTK, not chosen:

  - **A `ctkPathLineEdit` emits `currentPathChanged` only for input its name
    filters accept.** Restricting a picker to `*.csv` — which the schema asks
    for, since `types` names the accepted extensions — silently swallows the
    change signal for *every folder* (and every non-matching file), so Apply
    would never enable after choosing a folder. Measured against Slicer 5.13:
    with a `*.csv` filter, a file/folder/file/xlsx sequence notifies only for
    the `.csv` selections; filter order changes nothing. Driving both dialogs
    ourselves keeps the file dialog filtered by the declared extensions *and*
    every selection observable.
  - **Its browse button opens a file dialog or a directory dialog according to
    `filters`, and those cannot be flipped at runtime.** Re-assigning
    `nameFilters` on a live `ctkPathLineEdit` corrupts it: Slicer dies either
    on the next `filters` assignment or later, at teardown. (Toggling
    `filters` alone, or assigning `nameFilters` exactly once, are both fine.)

  Hence the rule enforced by `formgen.path_widget`, which still backs the
  single-kind modes: **a ctkPathLineEdit is configured once, at construction,
  and never touched again** — and an unrestricted picker is left with its
  default rather than handed an empty filter list. `test_formgen.py` guards
  both findings: it counts assignments to `filters`/`nameFilters`, and asserts
  every selection notifies whatever its kind.
- `"volume_node"` — a `qMRMLNodeComboBox` restricted to `vtkMRMLScalarVolumeNode`;
  the selected node is exported to `<workspace>/<tool>_<arg_name>.nii.gz` via
  `slicer_io.export_volume`.

The explicit modes remain for what the schema cannot express (picking a volume
from the MRML scene) or to force one selection kind — `SurgMovPred` keeps
`"folder_zip"` because its `input` is typed `zip_file`, with the "give me a
folder, I'll zip it" step being a client-side convention rather than something
the schema declares.

The derived set cannot drift, but an *override* can: it is written by hand
against a remembered schema, so one naming an argument the server no longer
declares as a file surfaces immediately as a visible warning in the panel
(`_warnAboutFileInputsMismatch`) rather than being silently ignored — or
failing later with a confusing 422.

`RESULT_KIND` — declared, or else derived from the tool's `output_kind` via
`formgen.result_kind_for` — controls `handleResult`'s default and whether an
"Output folder" field is shown. The resolved value is read through the
`resultKind` property, never off the class attribute:

- `"text"` → `slicer.util.infoDisplay(result.text)`.
- `"segmentation" | "volume" | "model"` → `slicer_io.load_result(result.path, kind)`.
  (`"model"` here is Slicer's `vtkMRMLModelNode` — a 3D surface mesh loaded via
  `loadModel` — unrelated to a machine-learning model; those live server-side
  entirely, e.g. SurgMovPred's `.joblib` files, and never reach the client.)
- `"save_as"` → an explicit output-folder picker is added; the result is
  written there. This is also what a tool declaring `output_kind: "files"`
  server-side needs — several result files arrive as one `.zip`, unpacked into
  the chosen folder. Since one HTTP response can only carry a single blob, a tool
  whose CLI writes several files (SurgMovPred's CLI writes both
  `predictions_outputs.xlsx` and `predictions_outputs.csv`) needs its
  server-side wrapper to zip `outputFolder` before returning it — so
  `_handleSaveAsResult` checks `slicer_io.is_extractable_archive(result.path)`
  and, if true, unzips it into the output folder and discards the archive,
  rather than leaving the user with one opaque `.gz`/`.bin` file. A tool that
  genuinely returns a single file (SurgMovPred currently returns just
  `predictions_outputs.xlsx`, not a zip) is left as-is.

  **`is_extractable_archive` decides purely from the filename's extension
  (`.zip`), never by sniffing the file's bytes for a zip signature.** This was
  a real bug: the first version checked `zipfile.is_zipfile(result.path)`,
  which is also `True` for `.xlsx`/`.docx`/`.ods`/`.pptx` — OOXML formats are
  zip containers internally. A returned `predictions_outputs.xlsx` was being
  silently "extracted" into its raw `[Content_Types].xml`/`_rels/`/`xl/` parts
  instead of being kept as the spreadsheet it is. Getting the extension right
  is therefore load-bearing, not cosmetic — see `_result_filename` above.

## `formgen.py`

The server's `/tools` response is the single source of truth for a tool's
scalar arguments. `build(arguments_schema, layout)` renders one row per
argument (skipping every file-type entry per `is_file_type` — those are
handled by `base_widget` according to `FILE_INPUTS`, not as generic scalar
fields) into a `qt.QFormLayout`, using the type table below, and returns
`{arg_name: widget}`. `collect(arg_widgets)` reads them back;
`all_required_filled(...)` drives the Apply button's enabled state;
`connect_changed(widget, callback)` wires the right Qt signal per widget type.

| Schema `type` | Qt widget |
|---|---|
| any non-file type with `server_selectable` set | `QComboBox` (populated by `base_widget._populateServerSelectables` from `GET /tools/{tool}/data` — `formgen` itself never talks HTTP) |
| any **file** type with `server_selectable` set, or one `accepts_volume` says yes to | `ServerFileInput`: a one-line row, [sources dropdown][local picker]. The dropdown offers a prompt, then the tool's server-hosted test files (each with its kind and size), then the scene's open volumes (see "The input row" below) |
| `str` | `QLineEdit` |
| `int` | `QSpinBox` (range/step bounded by `min`/`max`/`step` when declared), or a `ctkSliderWidget` when the spec says `ui: "slider"` and declares both bounds |
| `float` | `QDoubleSpinBox`, same rule, plus `decimals` |
| `bool` | `QCheckBox` |
| `vec2` | a `JoystickInput`: two `QDoubleSpinBox`es set together, plus the 2D `JoystickPad` when the spec says `ui: "joystick"` (see "Numbers" below) |
| `choice` | `QComboBox` filled from `choices` |
| `multichoice` | a `MultiChoiceGroup`: one `QCheckBox` per entry of `choices` |
| any type where `is_file_type()` is true (`file`, `zip_file`, `nifti_file`, ...) | `ctkPathLineEdit`, or a `FileOrFolderInput` when `types` also contains `"folder"` (`file_widget`; `build()` itself never emits one — see `FILE_INPUTS` and the escape hatch below) |
| (unknown, non-file) | `QLineEdit` + a logged warning |

`description` becomes the tooltip; `required: true` fields get an asterisk
label via `design.required_label`.

### Presentation hints — `label`, `section`, `visible_when`, `options_when`, `ui`, `groups`, `hidden`

Everything above answers *what* an argument is. Past a certain size that stops
being enough: ASO declares 130 CBCT landmarks, 32 teeth, 8 landmark types and
2 jaws in one schema, which the rules above render as a single column of ~180
check boxes with the CBCT and IOS options interleaved — while any given run
uses one half or the other. The old local module solved that with a
hand-written four-page `QStackedWidget`, which is exactly the
anatomy-in-the-widget this architecture exists to remove.

So the schema grew a set of **presentation** fields, published verbatim by
`GET /tools` and ignored by the server's own `validate()`. They are all
optional and all `null` on every tool declaring none — which is the
compatibility guarantee: **a tool declaring no hint renders exactly as it did
before they existed**, asserted for `example_tool` in `test_formgen.py`.

**Where they come from now.** For the two in-process demo tools they are fields
of the server's `ArgSpec`. For every clinical tool they are written in that
tool's own `layout.py`, in the `SADT-VISOR` repository, and merged into the
generated schema by `describe.py` — derived from the same catalog the tool
computes with, never restated. That matters here for one reason: **a landmark
added to a catalog gets its tab with no client release**, and cannot be
published without one.

**And they travel through a server that has to name each key.** There was a
period where the tools published these and the client read them and nothing
arrived: the server was dropping every key it did not list. A hint the server
does not name does not exist, which is why a new one is a change in three
repositories rather than two.

| Field | Read by | Effect |
|---|---|---|
| `label` | `formgen.label_for` | the text next to the widget. Absent → the argument name prettified (`output_suffix` → "Output suffix") |
| `section` | `formgen.section_of` / `sections_of` | which `ctkCollapsibleButton` the row goes in. Absent → `formgen.DEFAULT_SECTION` (`"Inputs"`), the one box a panel has always had. Boxes are created in the order the schema first mentions them |
| `visible_when` | `formgen.is_visible` | `{other_argument: value}` (a list means "any of these"); every entry must match. A row whose condition fails is hidden, label included |
| `options_when` | `formgen.allowed_options` | `{other_argument: {its value: [option, ...]}}` — narrows a choice's **own options** instead of hiding the field. `AREG`'s three automation modes are all meaningful, but IOS has no "Oriented + Fully-Automated"; without this the combo box offers it and the run fails at the end |
| `hidden` | `formgen.is_visible` | never rendered, whatever else the schema says. For arguments a clinician has no business setting — the device to run on, a tiling step size, a worker count. The tool still declares them and still applies its own defaults; they are the deployment's business |
| `ui` | `formgen.MultiChoiceGroup` / `_make_numeric_widget` / `_make_vec2_widget` | per-type presentation: how a `multichoice`'s boxes are laid out (`"tabs"`, `"grid"`, `"inline"`), `"slider"` on a bounded int/float, `"joystick"` on a vec2 |
| `groups` | `formgen.MultiChoiceGroup` | `{group name: [option, ...]}` for the two grouped layouts |

**The layouts change where the boxes are put and nothing else.**
`MultiChoiceGroup.boxes` is keyed and ordered by `choices` whatever the layout,
so `collect()`, `connect_changed()`, `all_required_filled()` and the JSON
`client.py` builds cannot tell them apart. That is what makes a layout safe to
add — a wrong one is ugly, never wrong on the wire — and it is asserted
directly (`MultiChoiceLayoutTest.test_every_layout_reads_back_identically`).

- `"tabs"` — a `QTabWidget`, one scrollable multi-column tab per group, for a
  catalog too long to scroll through in one piece. The grouping is the
  *server's* (ASO's cranial base / upper / lower), not a client-side guess.
- `"grid"` — one row per group, options as columns, in a horizontally
  scrolling area. For options whose **position** carries meaning: ASO asks for
  teeth "spread across the arch", and a column of 32 check boxes is the one
  layout that cannot show whether a selection is spread or clustered. It
  scrolls rather than wrapping, because wrapping an arch onto two lines
  destroys the very adjacency the layout exists to show — the old `ASO.ui` did
  the same, with a scroll area around `LayoutSemiIOS_tooth`.
- `"inline"` — one horizontal row, for a handful of short options.
- An **unknown** layout falls back to the flat column with a logged warning: a
  hint from a newer server must never be able to break an older client.
- An option no group mentions is rendered in a trailing `"Other"` group rather
  than dropped. The server rejects a group naming an option that does not
  exist, but not the reverse, and dropping one would hide a selection the tool
  genuinely offers.

Every `multichoice` with more than one option also gets an **All / None /
Default** row (`design.link_button`). "Default" restores the state the schema
declared — the old ASO module's per-mode `Suggest()` button, now on every tool,
with the suggestion living server-side where it belongs.

### Numbers: `min`/`max`/`step`, `ui: "slider"`, and the `vec2` joystick

`int` and `float` carry four more optional fields, all presentation-side like
the hints above (the server's own `validate()` ignores them): `min`, `max`,
`step`, and (float only) `decimals`.

- Declared **without** `ui: "slider"`, they only constrain the spin box:
  range, step, decimals. A bound a server adds for documentation can never
  silently switch the widget kind.
- With `ui: "slider"` **and both bounds**, the argument renders as a
  `ctkSliderWidget`, the slider + spin box combination the original
  extension's manual-alignment rows use (GreedyReg / MRI2CBCT: rotations
  ±180°, translations ±200 mm). An integer slider sets `decimals = 0` and
  reads back as an `int`; a float slider derives its decimals from `step`
  (0.05 → 2) unless `decimals` says otherwise.
- `ui: "slider"` without both bounds falls back to the spin box with a logged
  warning: an unbounded slider has no geometry, and a presentation hint must
  never be able to break the panel.

**`vec2`** is an argument *type*, not a hint: two numbers set together,
travelling as a JSON `[x, y]` pair (`client._stringify` encodes lists like it
already encoded the multichoice dict). It renders as `formgen.JoystickInput`:
two `QDoubleSpinBox`es (labeled from `x_label`/`y_label`, default "X"/"Y")
that ARE the value, plus, when the spec says `ui: "joystick"`, the 2D pad
ported from FlexReg's butterfly-patch corner controls
(`joystick.JoystickPad`, the one genuine QWidget subclass in the library;
painting and mouse handling cannot be composed from stock widgets). Optional
fields:

| Field | Meaning |
|---|---|
| `x_range` / `y_range` | `[left/bottom end, right/top end]` of each axis. Index 0 is drawn at the left/bottom by construction, so declaring the bounds inverted (`[15, -15]`) mirrors the axis. Absent or invalid → `(0, 1)` with a warning |
| `initial` | the `[x, y]` the panel opens at. Absent → the centre of both axes, a joystick's natural rest position |
| `step` | one wheel notch, arrow key, or spin-box step. Absent → a hundredth of each axis |
| `x_label` / `y_label` | the axis names shown next to the spin boxes |
| `x_labels` / `y_labels` | 2-element *end* labels drawn in the pad's gutters (`["R", "L"]`, `["POST", "ANT"]`), paired with the range by index |
| `spring_back` | the pad becomes relative: each drag deals a displacement onto the boxes (clamped by their own ranges) and the knob springs home on release, so repeated pushes never saturate against the ends |

The pad writes into the spin boxes and holds no state of its own: `value()`
reads the boxes, `connect_changed` wires the boxes, so drag, wheel, arrow
keys and typing are one code path, and a pad that is not built (no `ui`, or
an unknown hint from a newer server) changes nothing on the wire. Gestures
match FlexReg: absolute drag, Ctrl+drag five times finer, wheel = vertical
axis, Shift+wheel = horizontal, arrows one step each, double-click back to
the defaults. The geometry/value mapping is unit-tested in
`test_joystick.py`; the schema-to-widget contract in `test_formgen.py`.

### The input row: one picker, the tool's test files, open volumes

A file argument's row is ONE line, like the original modules. When the
argument has more than one possible source, the local picker comes wrapped in
`ServerFileInput` with a leading dropdown; the row is then
[sources dropdown][path field + browse buttons].

The dropdown's entries, in order:

1. **The prompt** (`ServerFileInput.CHOOSE_OPTION`, deliberately the path
   field's own placeholder, "Select a file or a folder"): the state where
   nothing has been picked *from this list*. It is not a source and not a
   mode. What says whether the argument has been given anything is the path
   field.
2. **The tool's server-hosted test files** (`GET /tools/{tool}/data`), each
   labelled with what it is and what it costs — `CBCT_FullyAuto  (folder,
   339 MB)` — from the `entries` half of that payload
   (`formgen.hosted_entry_label` / `human_size`). A size the server did not
   state shows nothing, never `0 B`.
3. **The scene's open scalar volumes**, for any argument
   `formgen.accepts_volume` says yes to (a volume-ish type name, or a
   published volume extension; a csv input never offers them). `base_widget`
   feeds the names (`_refreshSceneVolumes`, re-run on `enter()`, on scene
   node add/remove, and after a scene close) and keeps the name-to-node map;
   at upload time the chosen node is exported to a temporary `.nii.gz` and
   sent like any local file. formgen itself never touches the MRML scene.

Which kind is selected is decided by index, never by parsing the entry text,
and rebuilding either list preserves the current selection when it is still
offered. Picking any dropdown entry clears the path field and vice versa,
same mutual-exclusion rule as before.

**Picking a test file downloads it**, and that is the one mechanism there is.
There used to be two, answering the same question differently: this dropdown,
which sent the hosted NAME and left the file on the server, and a separate
"Test data" button that four modules (`AMASSS`, `ALI`, `ASO`, `AREG`) declared
by hand as `TEST_DATA = {argument: github_release_url}` and eleven did not.
The server knew nothing about the second, and only it put the scan where a
clinician could open it beside the panel — which is the whole reason to want
test data in a viewer.

So a hosted test file is now **an action, not a state**: `formgen` reports the
pick through the callback `base_widget` registers (`setHostedCallback`) — it
never speaks HTTP — and `base_widget._onHostedTestFile` does the rest:

- **on a `BackgroundJob`, always.** Naming a file cost nothing because it never
  moved; fetching one on the main thread freezes the whole Slicer window,
  measured at 21 minutes once. Progress goes to the same label a tool run uses.
- **into a session temp directory** (`slicer.util.tempDirectory()`, kept on the
  widget so there is one per session), never `~/Documents`: a 648 MB cohort
  someone clicked once to look at has not been asked for permanently.
- **cached by name**, so a second pick of the same entry issues no request.
  Staged in a `<name>.downloading` sibling and renamed at the end, so an
  interrupted download can never leave a half-extracted folder the cache check
  would mistake for a complete one.
- **a hosted folder is unpacked** (it arrives as a `.zip` the server builds,
  there being no other way to put a directory on a wire) and the row points at
  the unpacked directory. A hosted *file* that happens to be a `.zip` is left
  as the file it is. The `kind` comes from `entries`; an older server that
  publishes none falls back to "an extensionless name that really did arrive as
  an archive was a directory".
- **a single file is loaded into the scene** (`slicer_io.load_input`, volume or
  mesh by extension) — the point of downloading rather than naming. A **folder
  never is**: a forty-patient cohort would flood the scene. A load that fails is
  a log line: the input is filled in and the run works either way.

Once it lands the row holds an ordinary local path, so it is **uploaded** like
any other file. The wire shape changed with the mechanism.

**A hosted MODEL is the exception, and keeps the old behaviour.** ASO's
`reference` is a *file*-typed argument flagged `server_selectable: "model"`,
and the server deliberately refuses to stream a model — weights are selected
by name and used in place. `ServerFileInput.hosted_downloads` is False for
those, `server_name()` answers with the selection, and `collectArgs` sends it
as a plain form value exactly as before.

**Where the words come from, and the line between the two.** Everything
describing a *tool* is the server's: the field label (`label`), the section
title (`section`), the tab and chart-row names (`groups`), the option names
(`choices`), the tooltip (`description`). The client owns only its own chrome —
Apply, Cancel, Retry, "Output folder", All / None / Default, the `"Other"`
group for options no `groups` entry mentions, and the fallback labels below —
which exist on every panel regardless of tool and are translated through `_()`.

`formgen.label_for(name, spec)` is the single rule, and the fallback is
deliberately a *fallback*: `name.replace("_", " ").capitalize()` renders
`cbct_landmarks` as "Cbct landmarks" and has no way to turn `input` into
"Scan / Landmark Folder". There used to be **two** rules — `build()` used the
raw schema name while `base_widget` prettified it — so an ASO panel showed
"Reference" directly above "cbct_landmarks".

The one place the two sides must agree by convention rather than by data is the
`"Outputs"` box: the client creates it for the output-folder picker whenever
`resultKind == "save_as"` (`base_widget._OUTPUTS_SECTION`), and a tool that
wants its own arguments in the same box declares `section="Outputs"` — which
ASO's `output_suffix` does. A tool naming that section anything else gets a
second box below it rather than a merge.

`build()` gained two optional parameters for this and kept its signature
otherwise: `sections={name: QFormLayout}` routes each argument to its box, and
`rows` is an out-parameter filled with `{arg_name: (label, field)}` — the pair
a caller has to hide *together*. An out-parameter rather than a second return
value because the labels are created inside `build()` and a QFormLayout's label
for a field cannot be recovered reliably across PythonQt versions.

### The caption: which file, and what kind of file

The row shows a path, and a path is the wrong thing to show. A downloaded test
file lands on
`/tmp/Slicer-luciacev/ADTRemoteTestFiles2026-09-09_09+32+09.118/MG_test_scan.nii.gz`,
which the field renders as `:TestFiles2026-09-09_09+32+09.118/MG_test_scan.nii.gz`
— the NAME is the first thing elided, the kind is never visible at all, and the
dropdown has meanwhile gone back to its prompt because picking a hosted file is
an action rather than a state. Nothing on screen said what was loaded.

So `ServerFileInput` is a column now: the controls on one line, and under them a
muted caption that wraps rather than elides.

    MG_test_scan.nii.gz - NIfTI volume, 94 MB - test data, fetched to a temporary folder

Three sources, three sentences, because "what is in this field" has three
different answers: a local path is described by `describe_file`, an open volume
says so by name (nothing is on disk for it, and "no file chosen" would be a lie
about a satisfied argument), and a hosted MODEL names itself.

- **`FILE_KINDS` maps an extension to the words a clinician uses** — "NIfTI
  volume", "VTK surface", "Slicer markups". Longest suffix first, so `.nii.gz`
  never matches as `.gz`. A name that matches nothing gets **no** kind: a bare
  name beats a confident guess at what a file holds.
- **"test data, fetched to a temporary folder" is not decoration.** That file
  sits in a session directory swept on exit; a user who takes it for their own
  copy will look for it next week and not find it. Decided by NAME against the
  entries this row offered, not by directory — the panel owns where a download
  lands and this widget does not.
- **The full path becomes the field's tooltip**, so where it sits stays
  reachable without costing a second line.

### `choice` and `multichoice`

Both carry a `choices` dict — `{option_name: initial_state}`, `null` on every
other type. **Its key order is the declaration order and is preserved as-is,
never sorted**, in the widget and in what is read back.

| `type` | Widget | Items | Initial state | Read back as |
|---|---|---|---|---|
| `choice` | `QComboBox` | the keys of `choices` | the key whose value is `true` (the server guarantees exactly one) | the selected option's name, e.g. `"json"` |
| `multichoice` | N × `QCheckBox` | the keys of `choices` | each key's boolean | the complete `{option: checked}` dict |

Neither is a single Qt widget mapping 1:1 onto an argument, so `multichoice`
gets a small holder class, `MultiChoiceGroup` (a plain Python object, not a
`QWidget` subclass — PythonQt makes those awkward): it owns the container to
lay out (`row_widget(field)` returns it), the checkboxes in declaration order,
and `value()`. `FileOrFolderInput` follows the same shape. Both expose the
slice of the QWidget API `build()` and `base_widget` use on a field
(`setProperty`, `setToolTip`), so nothing upstream needs to know they are
special; `collect()`, `connect_changed()` and `all_required_filled()`
special-case them in one branch each.

A `multichoice` always reads back as *every* option, and is therefore always
"filled" as far as the Apply button is concerned — every box unchecked is a
meaningful selection, not a missing value. Turning that dict into a form field
is `client.py`'s job (JSON, never the `a,b` shortcut — see `run()` above), so
`formgen` stays free of any wire-format knowledge.

There is deliberately **no client-side handling of a value outside the list**:
the server answers 422 with an explicit `detail` (`Argument 'preview_format':
unknown option 'xml'. Expected one of: csv, json`) and that message is what the
user sees.

**Escape hatch**, not used by `SurgMovPred`: if a tool ever needs a
hand-written `.ui` (grouping, an MRML node selector, default values), give the
relevant widgets a Qt dynamic property named `serverArgName` matching the
schema argument name — the same mechanism as `SlicerParameterName` already
used by this repo's `.ui` files — and `collect()`/`connect_changed()` can be
pointed at those widgets instead of calling `build()`. This is documented,
not implemented.

## `design.py`

One dict of tokens per theme (`_LIGHT`/`_DARK`: `PRIMARY`, `DANGER`, `SUCCESS`,
`TEXT`, `TEXT_MUTED`, `BORDER`, `BACKGROUND`, `SURFACE`, `SURFACE_HOVER`,
`DISABLED_*`) plus a spacing scale, plus `_BUTTON_STOPS_LIGHT`/`_DARK`, the
(top, bottom) stops of the vertical button gradient every `.ui` of the
original SlicerAutomatedDentalTools paints its buttons with, one pair per
role (`primary`, `danger`, `success`, `secondary`) and state. `is_dark_mode()`
is the **only** place in the extension that inspects `slicer.app.palette()`
luminance. `tokens()` re-reads it every call, so `apply()`/the factories
always reflect the current mode; `base_widget` calls
`design.apply(self.uiWidget)` again in `enter()`, which is when a user
switching Slicer's theme and reopening the module will see it recompute.
(A live in-place recompute while the module is already open and visible is
not wired up — see "Known limitations".)

The base stylesheet (`apply()`) covers the widget family the original
styles: collapsible sections, labels, line edits, combo boxes, spin boxes,
check boxes (18 px indicator, primary fill with an inline-SVG check mark when
checked, no compiled Qt resource needed), sliders (8 px groove, 16 px round
handle, which also styles the slider inside a `ctkSliderWidget`), progress
bars, and **bare `QPushButton`s**: a browse button nobody styled comes out
looking like the original's Search buttons, and the factories' own
stylesheets win over the inherited rule where they apply.

Factories: `primary_button(text)`, `danger_button(text)`,
`success_button(text)` (the original GreedyReg's green Run/Save family),
`secondary_button(text)` (blue-gray utility), `compact_button(text)` (the
tight inline variant the one-line input rows use), `toggle_button(text)`
(checkable, blue → red while checked; flat on purpose, the two-state color
is the information), `section_title(text)`, `required_label(text)`,
`hint_label(text)`, `link_button(text)`, `warning_label(text)`,
`status_badge()` / `update_status_badge(label, ok)`, `progress_label()`.
The joystick pad's paint colors live here too (`pad_palette()`, `PAD_SIZE`),
so theme detection and color choices stay in this one file. Changing the
primary color across the whole extension is still an edit to this file alone.

## `worker.py`

`BackgroundJob(target, on_success, on_error, on_progress)` runs `target` on a
`threading.Thread`; the thread only ever puts `("progress"|"success"|"error",
payload)` tuples on a `queue.Queue`. A `qt.QTimer` (100 ms) on the main thread
drains the queue and invokes the callbacks there — so `on_success`/`on_error`,
which are the only places allowed to touch `slicer.*`/MRML, always run on the
main thread. `cancel()` stops the timer and marks the job so any
already-queued outcome is discarded; the underlying `requests.post` is not
actually interrupted (see limitations).

### The second timer, and why a worker thread needs one

There are **two** timers, and the second one is not scheduling — it is a
workaround for how Python is embedded in Slicer. Slicer's interpreter keeps the
GIL on the main thread while that thread waits inside Qt's event loop, which is
where it waits for all of a user's session. A Python worker thread therefore
barely runs at all. Measured in Slicer, one bytecode loop:

| the main thread is... | the worker manages |
|---|---|
| running the loop itself | 13,079,723 iterations/s |
| **idle in Qt** — every real session | **6,632 iterations/s** |
| ticking a 100 ms timer — the drain timer above | 453,957 iterations/s |
| napping 1 ms at a time — as shipped | 15,586,975 iterations/s |

The drain timer is not enough on its own: it lifts the worker by a factor of
68 and leaves it 29 times short. This is what made a 94 MB test file take
twenty seconds from a panel and under a second from a script, and it is why
every earlier measurement taken from a harness looked fine — a harness that
polls `processEvents()` runs Python on the main thread continuously, and hands
the GIL over by accident.

`time.sleep` releases the GIL for its duration, so `_yieldGil` sleeps and does
nothing else; a `qt.QTimer` at interval 0 keeps calling it for as long as the
job runs. Both timers start and stop together — a yield timer outliving its job
would keep the main thread napping for nothing. End to end, the same download
through the same `BackgroundJob`: **11.70 s → 0.32 s**.

What it costs the interface is close to nothing. A 10 ms timer standing in for
a repaint, measured over six seconds with a worker busy: median 10.3 ms → 12.3
ms, p95 15.7 ms → 12.7 ms, worst 22.5 ms → 24.9 ms. Under one frame either way.

### Telling the user something is happening

`progress_cb` alone is not enough, and the gap is not cosmetic. The worker
thread spends a tool run blocked inside **one** `requests.post`, and that call
*is* the run: minutes of remote inference with no bytes moving in either
direction and nothing for the thread to report. A panel that shows a message
from before the request and nothing after it reads as frozen — an AMASSS run
was cancelled at three minutes for exactly that reason, having done nothing
wrong, forty seconds from finishing.

Three pieces close it, and only the first can cover the inference phase:

- **`_startElapsedTimer` / `_renderProgress`** (`base_widget`) — a main-thread
  `qt.QTimer` ticking once a second, re-rendering the current phase with the
  elapsed time (`Sending request... — 2:14 elapsed`) into `_progressLabel`, a
  `design.progress_label()` under the Cancel button. It has to be a main-thread
  timer precisely *because* the worker cannot speak while blocked. Started in
  `onApplyButton`, stopped by `_teardownJob` — which every exit path (success,
  error, cancel) already goes through.
- **`progress_cb` during the download** — see `client.run` above. `_onJobProgress`
  stores the message as the current *phase* rather than printing it once, so the
  tick keeps re-rendering it instead of leaving a message frozen minutes ago.
- **`_showPhase(...)` around the extraction** in `_handleSaveAsResult`, followed
  by `slicer.app.processEvents()`. Deliberately independent of the timer:
  `_onJobSuccess` calls `_teardownJob` **before** `handleResult`, so the work
  after it has no timer left to render with. The `processEvents()` is what
  actually paints the label — without it Qt repaints only once the blocking
  extraction has already finished, which is precisely too late to be useful.

What this still does **not** give you is real progress during the inference
itself: the elapsed time proves the panel is alive, but the server exposes no
job/progress endpoint, so no client can know how far along nnUNet is. That
needs a server-side change, not a client one (see limitations).

## `slicer_io.py`

`TempWorkspace` is a context manager: `mkdtemp` on `__enter__`, `rmtree` on
`__exit__` regardless of exception. `export_volume`, `zip_folder`,
`unzip_folder`, and `load_result(path, kind)` (dispatch to `loadSegmentation`/
`loadVolume`/`loadModel`/`loadTransform`) are the only functions in the
extension that touch node I/O directly.

`zip_folder` picks its compression per member. A folder argument is zipped only
because HTTP has no notion of a folder, the archive is a container, not an
attempt to make the data smaller, so already-compressed members (`.nii.gz`,
`.zip`, the OOXML formats: see `_STORED_EXTENSIONS`, which mirrors the server's
own table in `file_utils.py`) are `ZIP_STORED` and everything else is deflated
at level 1. Measured on 105 MB of gzipped CBCT: **2.3 s to pack at the old
default level 6, 0.16 s now, for an archive of exactly the same size**, the
deflate was spending seconds of the user's CPU, on the main thread, before a
single byte went out, and the server paid it again inflating them.

## `transfer.py`

Bulk transfer, split out of `client.py` because what it does is concurrency and
byte offsets rather than protocol. Imports neither `slicer` nor `qt`; only
`requests` and the stdlib.

**Why it exists.** One HTTP request rides one TCP connection, and one TCP
connection to a remote server is bound by its congestion window long before it
is bound by anyone's bandwidth. Measured against the real server through a relay
capping each connection at 12 MB/s (which is what a congestion-window-limited
stream looks like from the application's side), for a 100 MB file:

| | upload | download |
|---|---|---|
| one request, one connection (the old path) | 9.1 s | 8.7 s |
| chunked/ranged, 4 connections (the default) | 2.5 s, **3.7x** | 2.5 s, **3.6x** |
| chunked/ranged, 8 connections | 1.5 s, **6.2x** | 1.4 s, **6.2x** |

On loopback the same change is still worth 1.5x on upload, from no longer
buffering the file in RAM and no longer making the server write it to disk
twice. Peak client RSS for a 100 MB upload went from **200 MB to 32 MB**:
`requests` reads a `files=` argument entirely into memory and then builds the
whole encoded multipart body next to it, so the old path held the scan twice
over before the first byte left.

- `upload_file(...)` → `upload_id`. Opens a session (`POST /uploads`), sends the
  parts over a `ThreadPoolExecutor`, returns the id that `run()` then references
  in the `__uploads__` form field. Each part carries `X-Part-SHA256` over the
  **decompressed** bytes, so the server verifies exactly what it writes to disk;
  since the parts tile the file, the whole upload is verified without either
  side making a second pass over it. Parts of files that are not already
  compressed (an uncompressed `.nii`, a `.vtk` mesh) go up `Content-Encoding:
  gzip` at level 1, roughly a third of the bytes, so roughly a third of the
  time on a remote link.
- A part that fails is retried; a whole failed pass re-reads `GET /uploads/{id}`
  for what the server is *actually* missing rather than re-sending what it
  thinks failed, since a part whose response was lost on the way back did land.
- `download_ranged(...)` pulls a result over concurrent `Range` requests,
  `os.pwrite`-ing each into its offset in a pre-`ftruncate`d file. Every span
  checks its own length before returning, `iter_content` ending early is what a
  connection cut mid-body looks like from here and raises nothing on its own -
  so a short range is one retried range instead of a silently truncated file.
  On total failure the partial file is **removed**: for a `.zip`, leaving it
  would let the caller unpack whatever central directory survived and deliver a
  subset of the results.
- A referenced result is released with `DELETE /results/{id}` from a `finally`
  (`_release_result`, retried once), so a download that failed halfway or an
  archive that failed its integrity check still tells the server the file can
  go. Neither is retryable from here, so there is nothing to keep it for. The
  server only offers a reference above 16 MB in the first place: below that it
  streams the result and deletes it when the response ends, which depends on
  nothing the client does, and parallel ranges would buy nothing at that size.
  Releasing never fails a finished run; the server's idle reaper is the
  backstop, and this is what keeps it from being the thing that usually fires.
- `UnsupportedByServer` is raised (before any byte travels) when the server has
  no `/uploads` endpoints, which is what makes this extension keep working
  against a deployment that has not been updated. `client.py` remembers the
  verdict per session and falls back to the single multipart request.
- Tuned via `config.py`: `TRANSFER_PARALLELISM` (4), `TRANSFER_CHUNK_MB` (8),
  `TRANSFER_COMPRESS` (True).

`test_transfer.py` drives all of this against a real `ThreadingHTTPServer` that
can drop parts, corrupt them, cut ranges short and refuse ranges altogether -
mocks prove nothing about concurrency or byte offsets.

## Runtime configuration: `ServerToolsSettings` + `settings_qt.py`

`config.py` holds the **compiled-in defaults**; users need a way to point the
extension at a different server (URL, API key, TLS verification, timeout)
without editing source and rebuilding. Two pieces:

- **`settings_qt.py`** (in `ServerToolsCoreLib`, not imported by `__init__.py`):
  reads/writes a `qt.QSettings()` group (`"ServerTools"`, one key per field).
  `QSettings` is Slicer's native prefs mechanism — an ini/plist file on disk,
  independent of the Slicer process, so it survives restarts. Kept out of
  `__init__.py` on purpose: it imports `qt`, and the package must stay
  importable outside Slicer for `client.py`'s unit tests (see the dependency
  rule above). Only Slicer-side code imports it directly.
  - `load_overrides()` → `{}` if the user never saved anything, otherwise only
    the fields that were actually saved.
  - `save_overrides(server_url, token, verify_tls, timeout)`,
    `clear_overrides()` ("restore defaults" — removes the whole group).
  - `apply_saved_overrides(client)` — applies `load_overrides()` onto a
    `ToolServerClient` via `configure()`, if any override exists.
- **`ServerToolsCore.py`** calls `apply_saved_overrides(get_client())` in its
  `__init__` — this runs once, when Slicer discovers the module at startup
  (before any tool module is opened), so a setting saved in a previous
  session is already active by the time the user opens e.g. `SurgMovPred`.
- **`ServerToolsSettings`** (new visible module, category
  `"Automated Dental Tools.Advanced"`, depends on `ServerToolsCore`): a plain
  `ScriptedLoadableModuleWidget` (not a `ServerToolWidgetBase` — its Apply/
  async-job machinery is for calling remote tools, not for a local save
  action) with four fields matching `config.py` 1:1 (Server URL, API key as a
  password-masked `QLineEdit`, Verify TLS checkbox, Timeout spinbox), styled
  via `design.py` for consistency. **Save** calls both `save_overrides(...)`
  (persists) and `client.configure(...)` (applies immediately, no restart
  needed — every module sees it since they all share the same `get_client()`
  singleton). **Restore defaults** calls `clear_overrides()` and
  `client.configure(...)` back to `config.py`'s values.

**Limitation**: changing the server while a tool module (e.g. `SurgMovPred`)
is already open updates its `client` (same singleton) but does **not**
retroactively rebuild that widget's already-built AUTO_UI form — `_buildAutoUI`
only runs once, in `setup()`. If the new server has a different schema for the
same tool name, the user needs to close and reopen the module (or use
Developer mode's "Reload") to see the new fields.

## `SlicerCloud` — deploying the server, not calling it

Every other module here is a thin GUI over a tool the server exposes.
`SlicerCloud` is the GUI over the **server itself**: clone it, check Docker,
start it, notice when it has fallen behind its remote and relaunch it, and
choose which tools' model bundles land on disk.

It exists because the answer to "how do I get a server?" was a page of
terminal instructions, and the tools are useless without one. Plug and play
means the first thing a clinician opens is this panel, and the last thing they
type is nothing.

### The logic lives server-side, on purpose

`SlicerCloudLib/deploy.py` is deliberately thin. Everything it does beyond the
bootstrap is a call to **`scripts/server_ctl.py` in the server repository**,
which it clones first. Two consequences, both wanted:

- the panel and a terminal do *exactly* the same thing, so a deployment bug
  cannot be fixed in one and left in the other;
- a fix to the deployment logic ships with the **server**, not with a new
  extension release — which matters when the thing being fixed is what stands
  the server up.

The only parts that cannot be delegated are the ones that run before the clone
exists: `probe_host()` (is git/docker/compose there at all) and `clone()`.
Nothing else is implemented client-side.

### The pipe protocol

`server_ctl.py --json` prints **one JSON object on stdout and narrates on
stderr**. That split is what lets a single call both stream a live log into
the panel *and* return a parsed result — a `docker compose up` on a fresh
host is fifteen minutes of layer pulls, and a panel showing nothing during
them reads as frozen and gets cancelled just before it would have worked.

`_spawn` therefore drains stderr on **its own thread** while the calling
thread reads stdout. Reading one pipe and then the other deadlocks as soon as
the writer fills the one nobody is reading, which is precisely what that
fifteen minutes of output does.

`cancel()` reaches the `Popen` and terminates it. `BackgroundJob.cancel()`
alone would only stop *listening* — and the thing being cancelled here is
often a 12 GB download that would keep coming down the wire.

### Dependency rule, again

`deploy.py` imports neither `slicer` nor `qt` — subprocess and the standard
library only, the same rule `client.py` follows and for the same reason: it is
unit-tested under plain `python3 -m unittest`. `SlicerCloud.py` is the panel
and may import whatever it likes.

It also may not `pip install` anything, ever, so `server_ctl.py` is standard
library only on its side too. `find_python()` picks the interpreter to run it
with, and checks `sys.executable` **last** and only when it looks like a
Python binary: inside Slicer that value can be the Slicer application, and
handing it to `subprocess` launches a second Slicer rather than running a
script — with no error message that says so.

### GPU or not, and why the compose file grew a second service

A compose device reservation is all-or-nothing: a service asking for an nvidia
device cannot start *at all* on a machine that has none, and an override file
cannot rescue it (compose merges the `devices` list rather than replacing it,
so `devices: []` leaves the reservation in place — measured). The server repo
therefore defines two services sharing one YAML anchor, `inference` and
`inference-cpu`, and `server_ctl.py` picks between them from whether **docker**
has an `nvidia` runtime — not from whether `nvidia-smi` exists on the host,
which says nothing about whether the container toolkit is installed.

### Choosing tool data

`server_ctl.py catalog` compares `data-manifest.yml` against what is actually
on disk and reports, per tool, **what a download would really transfer**. That
distinction is the whole feature: the manifest is ~29 GB, and "ALI: 12.3 GB"
shown next to an already-complete ALI is the number that makes someone skip a
tool they already have.

The download engine skips anything already present, so there is no separate
"resume" and no separate "add one more tool" — re-running *is* both. The
panel's selection is persisted in `QSettings`, so coming back to add a tool
six months later starts from what was ticked last time.

An empty selection is refused rather than treated as "everything", in
`deploy.download_data`. A stray click on a button labelled "Download selected"
must not start a 29 GB transfer.

### What it does to the rest of the extension

When the server comes up, the panel saves its URL and generated API key
through `settings_qt.save_overrides` and `client.configure()` — the same path
`ServerToolsSettings` uses, so every module immediately points at the new
server with no restart. It deliberately does **not** touch `verify_tls`: that
flag is irrelevant to an `http://localhost` URL, and clearing it here would
silently disable certificate checking for whatever `https://` server the user
points at next.

### Which branch gets deployed

`DEFAULT_REPO_URL` / `DEFAULT_BRANCH` in `deploy.py` are the compiled-in
defaults; the panel's Advanced box exposes both, and a saved `QSettings` value
wins over the constant. **Changing the constant therefore does nothing on a
machine that has already saved a branch** — that is what the field is for.

The subtle part is the clone, which is created exactly **once**: `clone()`
returns early when `server_ctl.py` is already there, and `update()` used to
fast-forward against whatever upstream the checked-out branch tracked. Pointing
a deployment at another branch after the first install therefore had *no
effect at all*, silently. So the configured branch travels on **every**
`status` and `update`: `status` reports a mismatch (the panel's clone row and
its next-step hint both say so), and `update` checks the clone out onto the
requested branch before doing anything else — refusing, like the pull does, to
move a tree with uncommitted changes.

That also forced a reordering in `server_ctl.cmd_update`: the git half now runs
**before** the docker preflight. Fetching new code needs neither a working
docker nor a free port, and those are precisely what a user may be updating in
order to fix — refusing to switch branch because port 8000 is busy is the tool
getting in its own way.

### Lifecycle: the container outlives Slicer unless told otherwise

`docker compose up -d` is detached, so nothing about the container is tied to
the Slicer process. Verified by `kill -9`-ing the parent: the container stays
up. Leaving the module does nothing (there is deliberately no `exit()`
override), and `cleanup()` — teardown only — cancels the running *subprocess*,
never the container. A reboot brings it back (`restart: unless-stopped`).

**"Stop the server when Slicer closes" (on by default)** is the answer to the
obvious objection: a clinician should not be left with a background process
they did not ask for. Idle it costs ~220 MB of RAM and no CPU or GPU (measured
— the tools' heavy imports are lazy, so torch is not even loaded), but after a
run the process keeps the loaded models resident until it restarts.

Three things make it work:

- **`stop`, never `down`.** The container is kept, so its writable layer keeps
  the `pip install --user` its command performed. A later start is ~7 s
  (measured) instead of the several minutes a fresh install takes. Removing
  the container is what would make dependencies reinstall.
- **Detached (`stop_detached`), not awaited.** A real `docker compose stop` on
  this image measures **10.5 s**: uvicorn runs with `--reload` and does not act
  on SIGTERM, so compose waits out the whole grace period and then SIGKILLs.
  Every one of those seconds would be Slicer refusing to close. Detaching
  takes the quit hook to **0.05 s**. Nothing is reported back, which is the
  honest shape of the operation — by the time it finishes there is no window
  to report into, and the failure mode is the container staying up, which is
  where we already were.
- **Hooked from the module class, not the widget.** `SlicerCloud.__init__`
  runs at Slicer startup for every discovered module and connects
  `aboutToQuit`. A widget only exists once someone has *opened* the panel, so
  a server started last Monday and never revisited — precisely the case this
  setting is for — would otherwise never be stopped.

The handler swallows everything: an exception raised in an `aboutToQuit`
handler is a crash on exit, for a background convenience.

Turn it off when the machine serves other people; closing Slicer would stop
their server too. That caveat is in the checkbox's tooltip, not only here.

### Installing Docker

Offered only where it can actually be delivered: Linux with a graphical
`pkexec`. What runs as root is `scripts/install-docker.sh` **from the clone**
— a file the user already has on disk and can read — not a curl-pipe-to-root
started by a button. Everywhere else the panel prints the exact command, or
the Docker Desktop link.

The one thing that has to be shouted rather than logged: adding a user to the
`docker` group only takes effect in a **new login session**, so a successful
install is followed by "log out and back in", not by a working server.

## `SurgMovPred`

```python
class SurgMovPredWidget(ServerToolWidgetBase):
    TOOL_NAME   = "surg_mov_pred"
    FILE_INPUTS = {"input": "folder_zip"}
    RESULT_KIND = "save_as"
    AUTO_UI     = True
```

That, plus the standard `ScriptedLoadableModule` metadata class, is the entire
file (~35 lines). No overrides are needed: the input folder of
`.csv`/`.xlsx`/`.ods` files is zipped automatically, and the result is written
to a user-chosen output folder.

**The model is selected on the server, not uploaded.** The server's
`surg_mov_pred` schema declares `"model"` as
`{"type": "str", "server_selectable": "model", "required": true}`: the client
sends the *name* of a model hosted in the server's data store, and the server
resolves it internally (`GET /tools/surg_mov_pred/data` lists what's
available). Client-side this needs zero SurgMovPred-specific code: any scalar
argument flagged `server_selectable` is rendered by `formgen` as a `QComboBox`
and populated by `base_widget._populateServerSelectables` via
`client.list_tool_data()` — the value collected is the selected name, sent as
a plain form field. An unreachable server or an empty model list surfaces as a
visible warning label in the panel, and the Apply button stays disabled while
the dropdown is empty (an empty `currentText` fails
`formgen.all_required_filled`). Uploading a local model package is not just
unsupported client-side, the server actively rejects a file upload for a
scalar argument with a 400.

**The same flag on a *file* argument means something different**, and which of
the two kinds it names decides what happens:

- **`server_selectable: "testfile"`** (ALI's and AMASSS's `input`, AREG's
  `t1`/`t2`) — the tool's test data. The caller may upload their own file **or**
  pick one of the hosted entries, and picking one **downloads it** (see "The
  input row" above), because the reason to want a test scan in Slicer is to
  look at it. It is then an ordinary local file and goes up as an upload.
- **`server_selectable: "model"` on a file type** (ASO's `reference`) — a
  bundle, not test data. It is never downloadable: the selection leaves
  `currentPath` empty on purpose, so `prepareInputFiles` uploads nothing and
  `collectArgs` sends the NAME as a plain form value.

Either way the two halves are kept mutually exclusive by clearing the other,
rather than letting one silently win, and `client._validate_against_schema`
accepts a name *or* an upload as satisfying a required file argument — the
client only sends the second shape for a test file now, but the server takes
both and a validator stricter than the thing it mirrors is a bug. An empty
hosted list is not warned about here — unlike a scalar model, a file argument
can always be uploaded instead.

(Historical note: an earlier iteration had
`"model"` as a second `zip_file` upload — `FILE_INPUTS = {"input":
"folder_zip", "model": "folder_zip"}` — because the server of the time
required it. The multi-file upload machinery it forced into `client.run()` /
`FILE_INPUTS` remains, and is still exercised by tests, for any future tool
with several genuine file inputs.)

### `SurgMovPred_CLI`

Left in the repository but **not** added to the root `CMakeLists.txt` — it is
out of the Slicer build's execution path, per the brief. Its `main(inputFolder,
modelPath, outputFolder)` is unchanged and is exactly what the server-side
tool wrapper for `surg_mov_pred` should call: create a temp dir, unzip the
uploaded archive into `inputFolder`, inject `modelPath` from the server's own
config, call `main()`, re-zip `outputFolder` into the HTTP response. That
wrapper is server-side and out of scope for this change.

## `ExampleTool`

```python
class ExampleToolWidget(ServerToolWidgetBase):
    TOOL_NAME = "example_tool"
```

The server's `example_tool` is the one tool exercising everything the client
has to know how to do, which makes this module the reference client — and a
quick way to check a server connection end to end without running a real
analysis. Its schema declares, and this module renders with **zero
tool-specific code**:

- `label` (str), `threshold` (float), `iterations` (optional int) — the
  ordinary scalar fields;
- `preview_format`, a `choice` → a combo box of `csv`/`json`, `csv`
  preselected, sent as `preview_format=json`;
- `outputs`, a `multichoice` → three checkboxes (`summary`, `preview`,
  `columns`) starting at `true, true, false`, sent as
  `outputs={"summary": true, "preview": false, "columns": true}`;
- `input`, typed `["csv_file", "folder"]` → one path field taking either; a
  `.csv` goes up as-is, a folder is zipped first, and which one it got is
  detected, not declared (`"auto"` resolves to `"file_or_folder"`);
- `output_kind: "files"` → the response is a `.zip` of several result files
  (`summary.txt`, `preview.json`, ...) named by `Content-Disposition`, unpacked
  into the output folder the user picks (derives `RESULT_KIND = "save_as"`).

Which is why the class body is one line: writing `FILE_INPUTS = {"input":
"auto"}` and `RESULT_KIND = "save_as"` would only repeat what the server
already said — `"auto"` literally means "ask the schema", and `files` can only
be saved. Verified in Slicer: loading this module builds the whole panel, and
`_inputModes` resolves to `{"input": "file_or_folder"}` with `resultKind` at
`"save_as"`, from `TOOL_NAME` alone.

## `ALI`

```python
class ALIWidget(ServerToolWidgetBase):
    TOOL_NAME   = "ALI"
    FILE_INPUTS = {"input": "file_or_folder"}
```

Plus a `configureFields` (one placeholder), an `addExtraWidgets` (one check
box) and a `handleResult` that reads the run report — about 250 lines, almost
all of it the report summary.

`FILE_INPUTS` is the one thing ALI's schema cannot state: `input` is typed
`("volume_or_zip_file", "surface_or_zip_file")` — two *file* types and no
`"folder"` — so `auto_file_mode` would give it a file picker only. But a
cohort, and **any** DICOM series, is a directory. The schema cannot add
`"folder"` either: `main.py` would then extract the archive itself, and the
client would have to guess which of the two kinds it is sending in order to
pick a file filter, which is exactly what it cannot know.

**ALI has no `mode` argument, deliberately, and that is why it has no
`visible_when`.** A `.zip` can hold volumes or meshes and a DICOM series has no
extension at all, so nothing in the request distinguishes them — only the data
does, and the server looks. The cost is that both engines' selections are
always rendered and one of them is inert on any given run. Since there is no
`choice` field to key visibility off, the schema does the next best thing:
`section` puts each engine's selection in its own collapsible box (Inputs /
CBCT landmarks / IOS landmarks / Outputs), so a CBCT user reads one box and
ignores the other instead of scanning a flat list.

**Two granularities for the same CBCT selection**, and the second one exists
for another tool: `cbct_regions` is four check boxes, which is what a human
placing a full set of points wants; `landmarks` is all 119 labels, rendered as
tabs (`ui="tabs"`, `groups` = the server's own `GROUP_LABELS`). Naming any
landmark *replaces* the region selection rather than narrowing it. ASO's
fully-automated CBCT mode registers on seven landmarks straddling two regions,
so going through regions would run 58 deep-RL agents to use seven — and the
argument is offered to the Slicer user too, which is what makes the tabs worth
having rather than a 119-row column.

`handleResult` is overridden rather than extended: the base `"save_as"` info
dialog would pop before `run_report.json` has been read, and that report is the
module's one real job. A landmark absent from the scene means one of two very
different things — *not in the selected bundle* (use another one) or *never
converged* (this scan is hard) — and only the report tells them apart, so the
summary is built around the failures and names both kinds separately.

## `TOOL_NAME` is the contract, and it is exact

A module's `TOOL_NAME` is what `/run/<name>` is built from, and the server
resolves it against the tool **folder name** in the `SADT-VISOR` repository.
There is no normalisation on either side: no case folding, no underscore
stripping, no aliasing. A mismatch is a `404` before the panel has drawn
anything, not a degraded form.

The naming convention on that side is: an acronym as it stands (`ALI_CBCT`,
`ASO`, `AMASSS`, `AREG`), anything else as capitalised words joined by
underscores (`Batch_Dental_Seg`, `Crown_Seg`, `Surg_Mov_Pred`). The in-process
demo tools that stayed on the server follow it too (`Test_Tool`,
`Example_Tool`).

> **Check every module against `GET /tools` after a server update.** Two are
> known to disagree today: `SurgMovPred/SurgMovPred.py` holds
> `TOOL_NAME = "SurgMovPred"` where the served tool is `Surg_Mov_Pred`, and
> `ExampleTool/ExampleTool.py` holds `"example_tool"` where it is
> `Example_Tool`. Both are one-line fixes; both are invisible until someone
> opens the module. `ALI` is the third to watch — it is `ALI_CBCT` and
> `ALI_IOS` now, which is a panel decision and not a rename.

## How to add a new module in 5 minutes

Worked example: migrating `AMASSS` (CBCT volume in, segmentation out).
Assume the server exposes a tool named `AMASSS` with one file-type argument
(`"scans": {"type": "path", "extensions": [".nii", ".nii.gz", ".nrrd"]}` — the
volume or a folder of them) plus whatever scalar options it needs — no
server-side change is asked of you, only what the client needs:

1. **CMakeLists.txt** (`AMASSS/CMakeLists.txt`) — drop the `.ui` resource
   entry (deleted, see below), keep the icon, no other change.

2. **Delete `AMASSS/Resources/UI/AMASSS.ui`** — the GUI is generated from the
   server schema now.

3. **Rewrite `AMASSS/AMASSS.py`**:

   ```python
   from slicer.i18n import tr as _
   from slicer.ScriptedLoadableModule import ScriptedLoadableModule
   from ServerToolsCoreLib.base_widget import ServerToolWidgetBase


   class AMASSS(ScriptedLoadableModule):
       def __init__(self, parent):
           ScriptedLoadableModule.__init__(self, parent)
           self.parent.title = _("AMASSS")
           self.parent.categories = ["Automated Dental Tools"]
           self.parent.dependencies = ["ServerToolsCore"]
           self.parent.contributors = ["..."]
           self.parent.helpText = _("CBCT segmentation, served remotely.")
           self.parent.acknowledgementText = ""


   class AMASSSWidget(ServerToolWidgetBase):
       TOOL_NAME   = "amasss_segmentation"
       # The one thing the schema can't say: this file argument is filled from
       # a volume already in the scene (exported to .nii.gz automatically),
       # not picked off disk. RESULT_KIND is left out — output_kind
       # "segmentation" already means "load it into the scene".
       FILE_INPUTS = {"file": "volume_node"}
       AUTO_UI     = True              # the "threshold" float field is generated from /tools
   ```

4. That's it. No CSS, no HTTP, no threading code, no `.ui` file to keep in
   sync with the server. If AMASSS later needs a "preview" button, add:

   ```python
   def addExtraWidgets(self, layout):
       button = design.primary_button(_("Preview"))
       button.clicked.connect(self._onPreview)
       layout.addWidget(button)
   ```

   without touching `setup()` — satisfying the "adding a custom button" cost
   target in the brief.

This confirms the four success criteria from the brief:
- **new module** = a ~15-line class + `CMakeLists.txt` change;
- **new field** = zero client-side lines (comes from the server schema);
- **custom button** = one `addExtraWidgets` override;
- **network/display bug fix** = edit `client.py` / `base_widget.py` once, every module gets it.

## Debatable decisions & known limitations

- **CMake pattern for `ServerToolsCoreLib`**: uses `slicerMacroBuildScriptedModule`
  with the library's files listed as extra `SCRIPTS`, mirroring
  `BATCHDENTALSEGLib` (already in this repo) rather than a raw
  `install(DIRECTORY ... DESTINATION ${Slicer_QTSCRIPTEDMODULES_LIB_DIR})`.
  This macro only registers exactly one Slicer module (`NAME`); the extra
  `SCRIPTS` are copied/byte-compiled alongside it, not turned into modules of
  their own. Chosen because it is a proven pattern already used in this exact
  codebase; documented here since the original brief suggested the manual
  `install()` route instead.
- **Multi-file support was added once it turned out to be needed now, not
  later**: the original plan assumed one file per request under a reserved
  `"file"` key, with multi-file support deferred as a documented future need.
  Testing against the real server showed `surg_mov_pred` already requires two
  independent file arguments (`"model"` + `"input"`), so `client.run()` and
  `FILE_INPUTS` were generalized immediately instead of shipping something
  that couldn't call the actual tool. There is no more reserved `"file"` key:
  every file argument is uploaded under its own schema argument name. This
  subsumes the single-file case (a schema whose one file argument happens to
  be named `"file"` still works exactly as before).
- **File-type detection is a suffix convention, not a fixed enum**: after
  `FILE_INPUTS` shipped, the real server's schema turned out to type its file
  arguments as `"nifti_file"` / `"zip_file"` — never the literal `"file"` used
  everywhere in the original brief's examples. Every exact-match check
  (`spec.get("type") == "file"`) was replaced by `is_file_type()`
  (`client.py`), which treats `"file"` and any `"..._file"` type as a file
  upload. This was necessary, not cosmetic: without it, `formgen.build()`
  rendered bogus `QLineEdit` rows for `"model"`/`"input"` *in addition to*
  their real `FILE_INPUTS` widgets, and `_validate_against_schema` demanded
  `"model"`/`"input"` as required *scalar* arguments (since they didn't match
  the literal string `"file"`), which would have made every real call to
  `surg_mov_pred` fail local validation. If the server ever introduces a
  differently-shaped file type name (not ending in `_file`), `is_file_type`
  needs a one-line update — everything downstream (`formgen`, `base_widget`)
  picks it up automatically since they all go through this single function.
- **`"auto"` file inputs degrade when the schema can't be fetched**: the mode
  is resolved from the argument's `types`, so with an unreachable server
  `formgen.auto_file_mode({})` falls back to `"single_file"` — the panel offers
  a file picker with no extension filter instead of the file/folder choice. The
  reason is already on screen (the schema-fetch warning label) and the module
  is unusable anyway without a server, so this is a degraded display rather
  than a silent wrong behavior. A module that must keep its picker regardless
  declares the concrete mode instead of `"auto"`.
- **`/tools` cache never auto-invalidates for a *working* panel**:
  `get_client()` is a singleton cached for the process lifetime. A panel that
  failed to build does refresh and rebuild itself (health check or the Retry
  button, see "The panel heals when the server comes back"), but one that built
  successfully keeps its schema: if the server changes a tool's arguments while
  Slicer is running, the user has to reopen the module (Developer mode's
  "Reload") or restart Slicer. Rebuilding a healthy panel on its own would
  discard whatever the user had typed into it, so it is deliberately not
  automatic.
- **No true server-side cancel**: `BackgroundJob.cancel()` discards the
  result and releases the UI immediately, but the in-flight `requests.post`
  keeps running against the server until it finishes or times out
  (`timeout=600`). A real cancel would need the server to expose a
  cancellation endpoint keyed by a request id. Note the consequence when a user
  cancels late: the download completes anyway and the result file is left in the
  output folder, but `handleResult` never runs, so a `save_as` archive stays
  zipped instead of being unpacked. The file is intact — it just looks like
  nothing arrived.
- **No real progress during the inference phase**: the elapsed-time tick shows
  the panel is alive, never how far along the run is, because a tool run is a
  single request whose response arrives only at the end. Reporting genuine
  progress means the server growing a job API (submit → poll → fetch), which is
  the same change a real cancel needs; both are worth doing together or not at
  all. Until then, the honest signal available to a client is elapsed time.
- **Schema fetch is synchronous**: `get_tool_schema()` inside `_buildAutoUI`
  runs on the main thread during `setup()` (i.e. opening the module). This is
  a deliberate choice — `GET /tools` is cheap and cached, and building the
  form needs the schema before the first paint — but it is technically a
  blocking network call, unlike `run()`. Capped at `_TOOLS_FETCH_TIMEOUT = 15`s
  (separate from the 600s tool-execution timeout) so a slow/unreachable server
  can only stall module opening briefly, not for minutes, before falling back
  to an empty schema — and that fallback is shown as a visible warning label
  in the panel itself (`design.warning_label`), not just logged, so "the
  module looks broken" always comes with a reason on screen. The same applies
  to any other exception raised while building the UI (`setup()` wraps
  `_buildAutoUI`/`buildCustomUI` and shows the exception instead of leaving a
  half-built, silently broken panel).
- **Theme recompute is on `enter()`, not live**: switching Slicer's
  application-wide theme while a ServerTools module is the currently visible
  one does not repaint it until the user leaves and re-enters the module (or
  reopens Slicer). `design.tokens()` itself always reflects the current mode;
  only the "when do we re-apply the stylesheet" question is coarse.
- **`SlicerCloud` deploys over plain HTTP, on loopback only**: the generated
  deployment publishes port 8000 on `127.0.0.1` and the panel configures
  `http://localhost:8000`. TLS on a self-signed loopback certificate buys
  nothing a Slicer user would verify, and would make the plug-and-play path
  fail on a certificate warning. The constraint that makes it acceptable is
  the bind address, which `server_ctl.py` writes into `.env` — the moment that
  server is published on a network address it is medical images in the clear,
  and it needs a TLS terminator in front. `server/SECURITY.md` says so; this
  is the client-side half of the same statement.
- **The panel cannot deploy to a *remote* host**: it drives a clone on the
  machine Slicer runs on. Standing a server up on the lab GPU box is the same
  `scripts/setup-server.sh` over ssh, and then this panel is only used for its
  "point every module at a server" half — which is what `ServerToolsSettings`
  already does. Remote deployment would mean shipping ssh credentials handling
  into a Slicer module, which is a different project.
- **A cancelled model download loses only the file in flight**: the download
  engine stages each item in a temp folder beside its destination and moves it
  into place only once complete, so an interrupted run never leaves a
  truncated model that the next run would report as "already present". The
  partial bytes of the one file being transferred are discarded, and re-running
  restarts that file from zero — there is no byte-range resume.
- **`SurgMovPred_CLI` is orphaned, not deleted**: kept in the repo per the
  brief, no longer wired into the CMake build or called by the widget. It is
  the reference implementation for the server-side tool wrapper.
- **`config.py` ships a placeholder token**: `API_TOKEN = "REPLACE_ME"` and a
  placeholder `SERVER_URL`. Both must be set to real values before this is
  deployed; do not commit a real production token to this file.
