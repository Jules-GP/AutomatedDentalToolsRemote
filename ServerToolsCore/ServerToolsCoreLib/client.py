"""The only class in the extension that speaks HTTP to the tool server.

Imports neither `slicer` nor `qt` - see ARCHITECTURE.md dependency rule. This
makes it testable in plain CI with `requests` mocked out (see
ServerToolsCore/Testing/Python/test_client.py).

Bulk transfer is the one thing this file delegates: `transfer.py` moves a big
input up in parallel parts and pulls a big result down in parallel ranges,
because one file over one connection is throughput-bound by that connection's
congestion window rather than by the link. Everything about WHICH bytes travel
and what they mean stays here; that module only moves them.
"""

import json
import logging
import mimetypes
import os
import time
import re
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import quote

import requests

from . import transfer
from .errors import ServerToolError, error_for_status

logger = logging.getLogger("ServerToolsCore.client")

# A health check feeds the status banner on every enter(); it must never hang
# for as long as a real tool run (self._timeout, up to 600s).
_HEALTH_CHECK_TIMEOUT = 10

# get_tool_schema() is called synchronously from a module's setup() (building
# the GUI needs the schema before the first paint) - a slow/unreachable server
# must not be able to freeze Slicer for up to 600s just to open a module.
_TOOLS_FETCH_TIMEOUT = 15

_CONTENT_DISPOSITION_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?')
_SERVER_MESSAGE_MAX_LEN = 500

# A result archive can weigh hundreds of MB (AMASSS: one .nii.gz + .vtk per
# structure and per scan). It is streamed to disk in chunks of this size, so
# the whole body is never held in Slicer's RAM.
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024

# Header asking the server to hand back a POINTER to the result instead of
# streaming it down the same connection that carried the run, so it can be
# pulled in parallel ranges (see transfer.download_ranged). A server that
# predates it ignores an unknown header and answers exactly as it always did,
# which is what makes this safe to send unconditionally.
_RESULT_DELIVERY_HEADER = {"X-Result-Delivery": "reference"}

# Connections the pool keeps alive per host. Must exceed the transfer
# parallelism, or the parallel parts queue up on each other inside urllib3 and
# the whole point is lost.
_CONNECTION_POOL_SIZE = 16

# Form field naming the inputs that already travelled through the upload
# endpoints, as {argument name: upload id}. Must match the server's own
# _UPLOADS_FIELD; double-underscored so it can never collide with a tool's
# argument name.
_UPLOADS_FIELD = "__uploads__"


def _download_message(received: int, expected: Optional[int], label: str = "results") -> str:
    """"Downloading results... 8.2 / 14.1 MB (58%)", or without the total when
    the server sent no usable Content-Length."""
    received_mb = received / (1024 * 1024)
    if not expected:
        return f"Downloading {label}... {received_mb:.1f} MB"
    expected_mb = expected / (1024 * 1024)
    percent = min(100, round(100 * received / expected))
    return f"Downloading {label}... {received_mb:.1f} / {expected_mb:.1f} MB ({percent}%)"


# What a packaged tool declares for a file-or-folder argument. One name, from
# the tool contract in SADT-VISOR; see is_file_type and accepts_folder.
PATH_TYPE = "path"


def _canonical_tool_name(name: str) -> str:
    """A tool name with case and separators removed, matching the server's rule.

    Only for LOOKUP. Anything sent to the server, or shown to a user, stays the
    name the schema published.
    """
    return "".join(character for character in name.lower() if character.isalnum())


def is_file_type(type_name: str) -> bool:
    """Whether a schema argument `type` denotes a file upload.

    The server is not limited to a generic "file" type - it can (and does,
    e.g. "nifti_file", "zip_file") use more specific type names to hint at
    what kind of file is expected. Treating any "..._file" type (plus the
    literal "file", for tools that don't bother being specific) as a file
    argument means a new file-ish type the server introduces later needs no
    client-side code change - the whole point of a schema-driven client.

    "path" is the exception that rule did not survive: it is what a PACKAGED
    tool (SADT-VISOR) declares for every file or folder it takes, and it ends
    in neither. Left out, such a tool's schema reports NO file arguments at
    all and the panel refuses to build:

        FILE_INPUTS declares ['scans'] but the server's 'AMASSS' schema
        doesn't have them as file arguments (it has: []).

    It is listed rather than pattern-matched because it is one name, fixed by
    the tool contract, and guessing at "anything not obviously scalar" would
    turn every unknown type into a file dialog.
    """
    return type_name in ("file", PATH_TYPE) or type_name.endswith("_file")


# Fallback only. The server publishes each file type's extensions in its
# `types`' company (see file_extensions_for), which is the single source of
# truth; this table is what a *pre-`extensions`* server leaves us guessing
# with, and it is a copy of that server's own FILE_TYPES - the kind of
# duplication that drifts. It once did: "volume_or_zip_file" was missing here
# and derived as ".volume_or_zip", a file dialog matching nothing.
#
# Do not grow it for a new type. Publish the type's extensions server-side
# instead; anything not listed still falls back to the obvious ".<x>"
# ("csv_file" -> ".csv") when the name spells one out.
_FILE_TYPE_EXTENSIONS = {
    "file": (),  # deliberately unrestricted: the generic type accepts anything
    "nifti_file": (".nii", ".nii.gz"),
    "zip_file": (".zip",),
    # A medical volume or a zip of a folder of them (AMASSS's `input`): the
    # type name doesn't spell out an extension, so it needs an entry here.
    "volume_or_zip_file": (".nii", ".nii.gz", ".nrrd", ".nrrd.gz", ".gipl", ".gipl.gz", ".zip"),
}

# The one non-file type that may appear alongside file types in `types`: it is
# a *local* selection kind, not something HTTP can carry - a folder is zipped
# client-side and uploaded as the .zip the server then unpacks.
FOLDER_TYPE = "folder"


def _guessed_extension(type_name: str) -> tuple:
    """The extension a "<x>_file" type name spells out, when it spells one.

    `"csv_file"` -> `(".csv",)`. But a compound name like
    `"volume_or_zip_file"` names a *set* of formats, not an extension: guessing
    `".volume_or_zip"` there produces a file dialog that matches nothing, which
    is worse than not filtering at all. Such a name (recognisable by the
    underscores left once "_file" is stripped) that has no entry in
    _FILE_TYPE_EXTENSIONS falls back to no restriction, so a new one the server
    introduces degrades to an unfiltered picker instead of an empty one.
    """
    if not type_name.endswith("_file"):
        return ()
    stem = type_name[: -len("_file")]
    return () if "_" in stem else (f".{stem}",)


def argument_types(spec: dict) -> list:
    """Every type a schema argument accepts.

    The server sends both a single `type` (the primary/first one) and the full
    `types` list; an argument accepting several - e.g. example_tool's `input`:
    `["csv_file", "folder"]` - is only fully described by the latter. Falls
    back to `[type]` so a schema predating the `types` field still works.
    """
    types = spec.get("types")
    if types:
        return list(types)
    type_name = spec.get("type")
    return [type_name] if type_name else []


def accepts_folder(spec: dict) -> bool:
    """Whether the user may pick a whole folder for this argument (which the
    client then zips before uploading - see slicer_io.zip_folder).

    A packaged tool's "path" always does: the tool contract requires every path
    argument to take a directory as readily as one file, because the server
    pays a process start-up cost per call and a cohort of forty scans has to be
    one run rather than forty."""
    types = argument_types(spec)
    return FOLDER_TYPE in types or PATH_TYPE in types


def file_extensions_for(spec: dict) -> tuple:
    """The extensions a file picker should offer for this argument - 
    `["csv_file", "folder"]` gives `(".csv",)`.

    Read from the schema's own `extensions` (`{type name: [extension, ...]}`,
    the server's FILE_TYPES table published alongside `types`), so the client
    holds no copy of it. Only the *file* types count: `"folder"`'s extensions
    say what a zipped folder may be uploaded as, not what a file picker should
    show.

    A server that predates the field leaves it out, and each type then falls
    back to _FILE_TYPE_EXTENSIONS or to what its name spells out.

    An empty tuple means "don't restrict": either the argument declares the
    generic "file" type, or it accepts no file type at all (folder only).
    """
    published = spec.get("extensions") or {}
    extensions = []
    for type_name in argument_types(spec):
        if not is_file_type(type_name):
            continue
        known = published.get(type_name, _FILE_TYPE_EXTENSIONS.get(type_name))
        if known is None:
            known = _guessed_extension(type_name)
        if not known:
            # Either the generic "file", or a type the server declines to
            # restrict: anything goes, so no filter at all.
            return ()
        extensions.extend(extension for extension in known if extension not in extensions)
    return tuple(extensions)


# The two shapes GET /tools/{tool}/data can answer with for its test files.
# `entries` is what a current server publishes -- a name, a `kind`
# ("file"/"folder") and a `size` in bytes; `testfiles` is the flat list of
# names every server has always sent. Both are read, so a panel built against
# the richer one keeps working against a server that only sends the older.
TESTFILE_KINDS = ("file", "folder")


def testfile_entries(data: dict) -> list:
    """`[{"name": str, "kind": str|None, "size": int|None}, ...]` for the test
    files a tool hosts, from a `list_tool_data` payload.

    `kind` and `size` are what let a picker say "folder, 339 MB" before
    fetching 339 MB, and BOTH may legitimately be absent: an older server
    publishes no `entries` at all, and a data-store backend that cannot size a
    tree cheaply sends `null`. Absent is therefore never an error and never a
    zero -- it is "unknown", which a caller renders as nothing rather than as
    "0 B".

    The flat `testfiles` list stays authoritative for WHICH names exist: it is
    the field every server sends, and an entry it does not mention is dropped
    rather than offered as a name the run endpoint would not resolve.
    """
    names = list(data.get("testfiles") or [])
    described = {}
    for entry in (data.get("entries") or {}).get("testfiles") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name:
            described[name] = entry

    entries = []
    for name in names:
        entry = described.get(name, {})
        kind = entry.get("kind")
        size = entry.get("size")
        entries.append({
            "name": name,
            "kind": kind if kind in TESTFILE_KINDS else None,
            "size": size if isinstance(size, int) and size >= 0 else None,
        })
    return entries


def _pooled_session() -> requests.Session:
    """One Session for every call this client makes, instead of a fresh
    connection per request.

    Two reasons, and the second is the load-bearing one. A module's setup()
    alone costs /health + /tools + /tools/{name}/data, each of which paid its
    own TCP and TLS handshake, several round trips against a remote server,
    every time a panel is opened. And a chunked transfer needs the pool to hand
    out as many connections as it has parts in flight; urllib3's default
    (10 per host, blocking above that) would quietly serialise them.
    """
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=_CONNECTION_POOL_SIZE,
        pool_maxsize=_CONNECTION_POOL_SIZE,
        # Retries stay with the callers: transfer.py resends the one part that
        # failed and knows what the server is still missing, which urllib3's
        # blind per-request retry cannot do.
        max_retries=0,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


@dataclass
class ToolResult:
    """Uniform result regardless of output_kind."""

    kind: str  # "text" | "file"
    text: Optional[str] = None
    path: Optional[str] = None


class ToolServerClient:
    def __init__(
        self,
        server_url,
        token,
        verify_tls=True,
        timeout=600,
        parallelism=transfer.DEFAULT_PARALLELISM,
        chunk_bytes=transfer.DEFAULT_CHUNK_BYTES,
        compress_uploads=True,
    ):
        self._server_url = server_url.rstrip("/")
        self._token = token
        self._verify_tls = verify_tls
        self._timeout = timeout
        self._parallelism = parallelism
        self._chunk_bytes = chunk_bytes
        self._compress_uploads = compress_uploads
        self._tools_cache = None
        # None until the first big upload tells us; False pins every later one
        # to the single-request path, so an old server costs one failed probe
        # per session rather than one per file.
        self._chunked_uploads = None
        self._session = _pooled_session()

    # ------------------------------------------------------------------
    # Live (re)configuration - e.g. from a user-facing settings panel
    # ------------------------------------------------------------------

    @property
    def server_url(self) -> str:
        return self._server_url

    @property
    def token(self) -> str:
        return self._token

    @property
    def verify_tls(self) -> bool:
        return self._verify_tls

    @property
    def timeout(self) -> int:
        return self._timeout

    def configure(self, server_url=None, token=None, verify_tls=None, timeout=None) -> None:
        """Update connection settings on the already-constructed singleton in
        place, so every module sharing get_client() sees the change without a
        Slicer restart. Drops the cached /tools schema unconditionally - it
        may belong to a different server entirely once any of these change.
        """
        if server_url is not None:
            self._server_url = server_url.rstrip("/")
        if token is not None:
            self._token = token
        if verify_tls is not None:
            self._verify_tls = verify_tls
        if timeout is not None:
            self._timeout = timeout
        self._tools_cache = None
        # Dropped for the same reason as the schema cache: "this server has no
        # chunked upload" is a fact about the server that just changed.
        self._chunked_uploads = None

    # ------------------------------------------------------------------
    # Schema discovery
    # ------------------------------------------------------------------

    def health(self) -> bool:
        try:
            response = self._session.get(
                f"{self._server_url}/health", timeout=_HEALTH_CHECK_TIMEOUT, verify=self._verify_tls
            )
            return bool(response.ok and response.json().get("status") == "ok")
        except (requests.RequestException, ValueError) as exc:
            logger.debug("Health check failed: %s", exc)
            return False

    def list_tools(self, force_refresh: bool = False) -> dict:
        """Return {tool_name: schema}, cached after the first call."""
        if self._tools_cache is None or force_refresh:
            self._tools_cache = self._fetch_tools()
        return self._tools_cache

    def get_tool_schema(self, tool_name: str, force_refresh: bool = False) -> dict:
        """`force_refresh` re-fetches /tools instead of trusting the cache - 
        used when retrying after a failure, where the cached list may be the
        very reason the tool wasn't found."""
        tools = self.list_tools(force_refresh=force_refresh)
        if tool_name in tools:
            return tools[tool_name]

        # Case and separators, before calling this unknown. A module names its
        # tool in a constant, and `SurgMovPred` became `Surg_Mov_Pred` when the
        # tool was packaged: the panel then showed "Unknown tool", which is what
        # a typo shows, for a rename that changed nothing else. The server
        # applies the same rule and refuses to serve two names that differ only
        # this way, so at most one can match here.
        canonical = _canonical_tool_name(tool_name)
        for served, schema in tools.items():
            if _canonical_tool_name(served) == canonical:
                return schema

        available = ", ".join(sorted(tools)) or "none"
        raise ServerToolError(f"Unknown tool '{tool_name}'. Available: {available}")

    def list_tool_data(self, tool_name: str) -> dict:
        """Return {"models": [...], "testfiles": [...], "entries": {...}} - what
        the server hosts for this tool (GET /tools/{tool}/data, Bearer-protected).

        This is what lets a server_selectable argument (e.g. SurgMovPred's
        "model") be offered as a dropdown of server-side choices instead of a
        local file picker. Not cached: called once per module setup(), and the
        server-side list can change independently of the /tools schema.

        `entries` is the richer half, added by a later server: the same names
        with a `kind` ("file"/"folder") and a `size` in bytes, so a test-file
        picker can say what it is about to fetch. Passed through verbatim and
        normalised by `testfile_entries`, which also covers a server that sends
        only the flat name lists.
        """
        try:
            response = self._session.get(
                f"{self._server_url}/tools/{tool_name}/data",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=_TOOLS_FETCH_TIMEOUT,
                verify=self._verify_tls,
            )
        except requests.RequestException as exc:
            raise ServerToolError(f"Could not reach the tool server: {exc}") from exc

        if not response.ok:
            raise error_for_status(response.status_code, self._server_message(response))

        try:
            data = response.json()
        except ValueError as exc:
            raise ServerToolError(f"Malformed response from the tool server: {exc}") from exc

        logger.info(
            "GET %s/tools/%s/data -> %d model(s), %d testfile(s)",
            self._server_url, tool_name, len(data.get("models", [])), len(data.get("testfiles", [])),
        )
        entries = data.get("entries")
        return {
            "models": data.get("models", []),
            "testfiles": data.get("testfiles", []),
            # {} rather than None for an older server, so every caller can
            # index it without asking which server it is talking to.
            "entries": entries if isinstance(entries, dict) else {},
        }

    def download_testfile(
        self,
        tool_name: str,
        filename: str,
        destination: str,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Fetch one of the tool's server-hosted test files to `destination`.

        `GET /tools/{tool}/testfiles/{name}`, Bearer-protected. A hosted
        *folder* arrives as a .zip the server builds for us; unpacking it is
        the caller's business, this only moves the bytes.

        Pulled over parallel byte ranges, the same path a large result takes
        (`_download_reference`): these are whole cohorts -- the CBCT
        semi-automated set is 648 MB -- and one connection is bound by its own
        congestion window long before it is bound by the link. Falls back to a
        single streamed read when the server does not advertise ranges, so it
        is safe against any server that serves the endpoint at all.
        """
        # quote with no safe characters: the name comes from the server's own
        # listing, but it lands in a URL path and a stray "/" or "?" in it must
        # address the same file rather than a different route.
        url = f"{self._server_url}/tools/{tool_name}/testfiles/{quote(filename, safe='')}"
        headers = {"Authorization": f"Bearer {self._token}"}
        label = f"Downloading {filename}..."

        # Timed in three pieces, because "the download took sixteen seconds"
        # can mean the HEAD, the transfer, or the disk, and only one of the
        # three is worth optimising. Measured against curl on the same file the
        # server answers in 0.17 s.
        probe_started = time.perf_counter()
        size = transfer.probe_ranged(
            self._session, url, headers=headers, verify_tls=self._verify_tls
        )
        probe_took = time.perf_counter() - probe_started
        if size and size >= transfer.MIN_CHUNKED_BYTES:
            body_started = time.perf_counter()
            streams = self._parallelism
            transfer.download_ranged(
                self._session,
                url,
                destination,
                size,
                headers=headers,
                verify_tls=self._verify_tls,
                parallelism=streams,
                chunk_bytes=self._chunk_bytes,
                progress_cb=progress_cb,
                label=label,
            )
            body_took = time.perf_counter() - body_started
            parts = sorted(transfer.last_part_times)
            spread = ""
            if parts:
                spread = "; {} parts, fastest {:.2f}s, median {:.2f}s, slowest {:.2f}s".format(
                    len(parts), parts[0], parts[len(parts) // 2], parts[-1]
                )
            print(
                "[transfer] {} ranged {} stream(s), {:.1f} MB: probe {:.2f}s, "
                "body {:.2f}s = {:.1f} MB/s{}".format(
                    filename, streams, size / 1048576,
                    probe_took, body_took, size / 1048576 / max(body_took, 1e-9),
                    spread,
                )
            )
            logger.info(
                "GET %s -> %d byte(s) saved to %s (ranged, %d stream(s), "
                "probe %.2fs, body %.2fs)",
                url, size, destination, streams, probe_took, body_took,
            )
            return destination

        # The sequential fallback. Which of the two paths ran is the first
        # thing anyone asks when a transfer is slow, and until this print the
        # log said nothing at all when it was this one.
        body_started = time.perf_counter()
        try:
            response = self._session.get(
                url,
                headers=headers,
                stream=True,
                timeout=self._timeout,
                verify=self._verify_tls,
            )
        except requests.RequestException as exc:
            raise ServerToolError(f"Could not reach the tool server: {exc}") from exc

        with response:
            if not response.ok:
                raise error_for_status(response.status_code, self._server_message(response))
            expected = self._expected_length(response) or 0
            received = 0
            with open(destination, "wb") as out_file:
                for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                    out_file.write(chunk)
                    received += len(chunk)
                    if progress_cb:
                        progress_cb(_download_message(received, expected, filename))
        body_took = time.perf_counter() - body_started
        print(
            "[transfer] {} sequential (probe said {}), {:.1f} MB: probe {:.2f}s, "
            "body {:.2f}s = {:.1f} MB/s".format(
                filename, size if size else "no ranges", received / 1048576,
                probe_took, body_took, received / 1048576 / max(body_took, 1e-9),
            )
        )
        logger.info(
            "GET %s -> %d byte(s) saved to %s (sequential, probe %.2fs, body %.2fs)",
            url, received, destination, probe_took, body_took,
        )
        return destination

    def _fetch_tools(self) -> dict:
        try:
            response = self._session.get(
                f"{self._server_url}/tools", timeout=_TOOLS_FETCH_TIMEOUT, verify=self._verify_tls
            )
        except requests.RequestException as exc:
            raise ServerToolError(f"Could not reach the tool server: {exc}") from exc

        if not response.ok:
            raise error_for_status(response.status_code, self._server_message(response))

        try:
            tools = response.json()
        except ValueError as exc:
            raise ServerToolError(f"Malformed response from the tool server: {exc}") from exc

        by_name = {tool["name"]: tool for tool in tools}
        logger.info("GET %s/tools -> %d tool(s): %s", self._server_url, len(by_name), sorted(by_name.keys()))
        return by_name

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(
        self,
        tool_name: str,
        args: Optional[dict] = None,
        files: Optional[dict] = None,
        output_dir: Optional[str] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> ToolResult:
        """`files`: {schema_argument_name: local_file_path}, one entry per
        `type: "file"` argument you're providing. Each is uploaded as its own
        multipart field named after its schema argument - a tool can declare
        several independent file arguments (e.g. SurgMovPred's "model" +
        "input"); there is no single reserved "file" key."""
        args = args or {}
        files = files or {}
        schema = self.get_tool_schema(tool_name)
        self._validate_against_schema(schema, args, files)

        headers = {"Authorization": f"Bearer {self._token}"}
        data = self._stringify(args)

        # Anything big enough to be worth it goes up FIRST, in parallel parts,
        # and this request then only references it. What stays in `files` is
        # what is small enough that a second and third round trip would cost
        # more than the single-connection upload does.
        files, upload_references = self._upload_large_inputs(files, progress_cb)
        if upload_references:
            data[_UPLOADS_FIELD] = json.dumps(upload_references)

        if progress_cb:
            progress_cb(f"Sending '{tool_name}' request...")

        # Debug visibility only: argument/file *names*, never the token or the
        # argument/file contents. Silent unless the caller has raised this
        # logger's level (see ARCHITECTURE.md "How to inspect a request").
        logger.debug(
            "POST %s/run/%s | arg keys=%s | file args=%s | pre-uploaded=%s",
            self._server_url,
            tool_name,
            sorted(data.keys()),
            {name: os.path.basename(path) for name, path in files.items()},
            sorted(upload_references),
        )

        file_handles = []
        try:
            files_payload = {}
            for arg_name, path in files.items():
                file_handle = open(path, "rb")
                file_handles.append(file_handle)
                # The filename (with extension) must travel with the upload: the
                # server validates extensions (.nii/.nii.gz/...) from it. Without
                # it, requests defaults to a bare filename and every upload with
                # an extension check fails server-side.
                files_payload[arg_name] = (os.path.basename(path), file_handle)

            try:
                # stream=True: the body is NOT downloaded here but inside
                # _build_result, chunk by chunk straight to disk. Without it,
                # requests buffers the entire result archive in RAM before a
                # single byte can be written -- the larger a run's output, the
                # closer that gets to taking Slicer down with it. The read
                # timeout then applies between chunks, not to the whole
                # download, so a big-but-flowing response can never time out
                # merely for being big.
                response = self._session.post(
                    f"{self._server_url}/run/{tool_name}",
                    headers={**headers, **_RESULT_DELIVERY_HEADER},
                    data=data,
                    files=files_payload or None,
                    timeout=self._timeout,
                    verify=self._verify_tls,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise ServerToolError(f"Network error while calling '{tool_name}': {exc}") from exc
        finally:
            for file_handle in file_handles:
                file_handle.close()

        logger.debug(
            "Response from %s: status=%s content-type=%s",
            tool_name,
            response.status_code,
            response.headers.get("Content-Type"),
        )

        if progress_cb:
            progress_cb("Processing response...")

        return self._build_result(tool_name, response, schema, output_dir, progress_cb)

    # ------------------------------------------------------------------
    # Bulk transfer (see transfer.py for why it is not one request)
    # ------------------------------------------------------------------

    def _upload_large_inputs(self, files: dict, progress_cb) -> tuple:
        """Split `files` into what still travels inside the /run request and
        what has already been sent through the upload endpoints.

        Returns `(remaining_files, {argument name: upload id})`. Falls back
        wholesale the moment a server turns out not to have the endpoints, so
        this extension keeps working against a deployment that has not been
        updated, that fallback is the reason the return is a pair rather than
        an in-place mutation.
        """
        if self._chunked_uploads is False:
            return files, {}

        remaining = dict(files)
        references = {}
        for arg_name, path in files.items():
            if not transfer.should_chunk(path, max(self._chunk_bytes * 2, 1)):
                continue
            try:
                references[arg_name] = transfer.upload_file(
                    self._session,
                    self._server_url,
                    {"Authorization": f"Bearer {self._token}"},
                    path,
                    verify_tls=self._verify_tls,
                    parallelism=self._parallelism,
                    chunk_bytes=self._chunk_bytes,
                    compress=self._compress_uploads,
                    progress_cb=progress_cb,
                )
            except transfer.UnsupportedByServer:
                logger.info(
                    "%s has no chunked-upload endpoints; falling back to a single request",
                    self._server_url,
                )
                self._chunked_uploads = False
                # Whatever went up before this file did is still valid and is
                # still referenced; only the rest reverts to multipart.
                break
            self._chunked_uploads = True
            remaining.pop(arg_name)
        return remaining, references

    def _download_reference(
        self,
        tool_name: str,
        reference: dict,
        output_dir: Optional[str],
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> ToolResult:
        """Fetch a result the server kept for us, over parallel byte ranges."""
        if not output_dir:
            raise ServerToolError("An output directory is required to save the returned file.")
        result_id = reference.get("result_id")
        if not result_id:
            raise ServerToolError(f"Malformed result reference from '{tool_name}'.")

        os.makedirs(output_dir, exist_ok=True)
        # basename, always: the name is the server's to choose, and a path
        # separator in it would otherwise write outside the output folder.
        filename = os.path.basename(reference.get("filename") or "") or f"{tool_name}_result.bin"
        dest_path = os.path.join(output_dir, filename)
        size = int(reference.get("size") or 0)

        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{self._server_url}/results/{result_id}"
        try:
            transfer.download_ranged(
                self._session,
                url,
                dest_path,
                size,
                headers=headers,
                verify_tls=self._verify_tls,
                parallelism=self._parallelism,
                chunk_bytes=self._chunk_bytes,
                progress_cb=progress_cb,
            )
            self._verify_archive(tool_name, dest_path)
        finally:
            # In a `finally`, and this is the point: the server keeps the
            # result until somebody says it can go, so every way out of this
            # method has to say it -- a download that failed halfway and a
            # result archive that failed its integrity check are exactly the
            # cases where a `return`-only cleanup would leave patient data
            # sitting on the server until the reaper got to it. Neither is
            # retryable from here (the reference is single-use), so there is
            # nothing to keep it for.
            self._release_result(url, headers, result_id)

        logger.info(
            "GET %s -> %d byte(s) saved to %s (ranged, %d stream(s))",
            url, size, dest_path, self._parallelism,
        )
        return ToolResult(kind="file", path=dest_path)

    def _release_result(self, url: str, headers: dict, result_id: str) -> None:
        """Tell the server it can delete the stored result.

        Retried once, because this is the difference between the file going
        away now and it lingering until the server's idle reaper collects it,
        and a single dropped packet should not decide that. Still best effort
        in the end: it must never turn a finished run into a failed one, and
        the reaper is the guarantee behind it -- this is what makes that
        guarantee almost never the thing that has to fire.
        """
        for attempt in range(2):
            try:
                response = self._session.delete(
                    url, headers=headers, timeout=_TOOLS_FETCH_TIMEOUT, verify=self._verify_tls
                )
                if response.ok or response.status_code == 404:
                    return
                logger.debug(
                    "server refused to release result %s: HTTP %d", result_id, response.status_code
                )
            except requests.RequestException as exc:
                logger.debug("could not release result %s (attempt %d): %s", result_id, attempt, exc)
        logger.warning(
            "Result %s could not be released; the server will reap it after its idle timeout.",
            result_id,
        )

    def _build_result(
        self,
        tool_name: str,
        response,
        schema: dict,
        output_dir: Optional[str],
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> ToolResult:
        # run() sends the request with stream=True, so the body has not been
        # read yet: .json()/.text below consume it for the small responses,
        # the iter_content loop consumes it for file results, and close() in
        # the finally releases the connection on every path.
        try:
            if not response.ok:
                raise error_for_status(response.status_code, self._server_message(response))

            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ServerToolError(f"Malformed response from the tool server: {exc}") from exc
                # A file result the server agreed to hand over by reference
                # (see _RESULT_DELIVERY_HEADER): the bytes are still on the
                # server and come down next, in parallel. Anything else is a
                # "text" tool's answer, exactly as before.
                if payload.get("result_ref"):
                    return self._download_reference(
                        tool_name, payload["result_ref"], output_dir, progress_cb
                    )
                return ToolResult(kind="text", text=payload.get("result"))

            if not output_dir:
                raise ServerToolError("An output directory is required to save the returned file.")

            os.makedirs(output_dir, exist_ok=True)
            dest_path = os.path.join(
                output_dir, self._result_filename(tool_name, response, schema, content_type)
            )
            expected_bytes = self._expected_length(response)
            received = 0
            with open(dest_path, "wb") as fh:
                for chunk in response.iter_content(_DOWNLOAD_CHUNK_BYTES):
                    fh.write(chunk)
                    received += len(chunk)
                    if progress_cb:
                        progress_cb(_download_message(received, expected_bytes))
            self._verify_download(tool_name, response, dest_path, received)

            # INFO on purpose (the request-shape logs above are DEBUG): this
            # is the one line that decides, after the fact, whether a
            # missing-results report is a transfer problem or a server one.
            # Only sizes and headers -- never the file's contents.
            logger.info(
                "POST %s/run/%s -> %d byte(s) saved to %s (Content-Type: %s, Content-Disposition: %s)",
                self._server_url,
                tool_name,
                received,
                dest_path,
                content_type or "<none>",
                response.headers.get("Content-Disposition") or "<none>",
            )
            return ToolResult(kind="file", path=dest_path)
        finally:
            response.close()

    @staticmethod
    def _expected_length(response) -> Optional[int]:
        """The download's total size, when it can be trusted.

        None whenever the body is transfer-compressed: Content-Length then
        counts wire bytes while what lands on disk is the decompressed stream,
        so using it would report a progress percentage running past 100.
        """
        if response.headers.get("Content-Encoding"):
            return None
        raw = response.headers.get("Content-Length")
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @classmethod
    def _verify_download(cls, tool_name: str, response, dest_path: str, received: int) -> None:
        """A file result must arrive complete or fail loudly, whatever its size.

        Without this, a connection dropped mid-body leaves a truncated file on
        disk; for a .zip the base widget then unpacks whatever central
        directory survives, silently delivering a SUBSET of the results -- the
        worst possible failure for medical data. The partial file is removed
        before raising, so no later step can pick it up by accident.
        """
        expected_bytes = cls._expected_length(response)
        if expected_bytes is not None and received != expected_bytes:
            os.remove(dest_path)
            raise ServerToolError(
                f"Truncated result from '{tool_name}': received {received} of "
                f"{expected_bytes} bytes. Nothing was kept; run the tool again."
            )
        cls._verify_archive(tool_name, dest_path)

    @staticmethod
    def _verify_archive(tool_name: str, dest_path: str) -> None:
        """CRC-check every member of a result .zip.

        Catches corruption that a matching byte count cannot (and truncation
        too, when the server never sent a Content-Length). Reads the archive
        once from local disk -- seconds, next to an inference measured in
        minutes -- and it is what stands between a half-transferred archive and
        the base widget unpacking whatever central directory survived, silently
        delivering a SUBSET of the results.
        """
        if not dest_path.lower().endswith(".zip"):
            return
        try:
            with zipfile.ZipFile(dest_path) as archive:
                corrupt = archive.testzip()
        except zipfile.BadZipFile as exc:
            os.remove(dest_path)
            raise ServerToolError(
                f"The result archive from '{tool_name}' is unreadable "
                f"(incomplete transfer?): {exc}. Nothing was kept; run the tool again."
            ) from exc
        if corrupt is not None:
            os.remove(dest_path)
            raise ServerToolError(
                f"The result archive from '{tool_name}' failed its integrity check "
                f"at '{corrupt}'. Nothing was kept; run the tool again."
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _result_filename(tool_name: str, response, schema: dict, content_type: str) -> str:
        """Prefer the server-provided filename (Content-Disposition); otherwise
        derive one from the schema's output_kind so file loaders that key off the
        extension (e.g. slicer.util.loadSegmentation expects .nii/.nii.gz) work."""
        content_disposition = response.headers.get("Content-Disposition", "")
        match = _CONTENT_DISPOSITION_FILENAME_RE.search(content_disposition)
        if match:
            # basename: the header is the server's to write, and a path
            # separator in it would place the result outside output_dir.
            name = os.path.basename(match.group(1).strip())
            if name:
                return name

        if schema.get("output_kind") == "segmentation":
            extension = ".nii.gz"
        else:
            # Mirror the server's own mimetypes.guess_type(): derive a real
            # extension from Content-Type instead of a generic .bin/.gz guess.
            # This keeps extension-based decisions downstream (e.g.
            # slicer_io.is_extractable_archive) correct even without a
            # Content-Disposition header.
            bare_content_type = content_type.split(";", 1)[0].strip()
            extension = mimetypes.guess_extension(bare_content_type) if bare_content_type else None
            if not extension:
                extension = ".gz" if "gzip" in content_type else ".bin"
        return f"{tool_name}_result{extension}"

    @staticmethod
    def _server_message(response) -> Optional[str]:
        """For 400/422 the server's own message must be propagated verbatim. Try
        JSON's "detail"/"message" first (FastAPI-style errors), then fall back to
        the raw response body so a plain-text error is never silently dropped."""
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            message = payload.get("detail") or payload.get("message")
            if message:
                return message

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            return None
        return text[:_SERVER_MESSAGE_MAX_LEN]

    @staticmethod
    def _stringify(args: dict) -> dict:
        """Every scalar becomes a string; the server does the type coercion.

        A "multichoice" argument arrives here as the *complete* {option:
        checked} dict (see formgen.MultiChoiceGroup) and is sent as JSON. Two
        things about that are load-bearing:

        - The whole dict travels, unchecked options included. Server-side, what
          is sent *is* the selection: an option left out counts as unchecked
          whatever its declared default, and omitting the argument entirely is
          what applies the defaults. So "everything unchecked" and "argument
          absent" are different requests, and only the real state of the boxes
          can tell them apart.
        - JSON, not the `a,b` shortcut. The server also accepts a
          comma-separated list of the checked options, but that spelling is for
          curl: it breaks the moment an option name contains a comma.
        """
        stringified = {}
        for key, value in args.items():
            if isinstance(value, bool):
                stringified[key] = "true" if value else "false"
            elif isinstance(value, (dict, list, tuple)):
                # dict: the multichoice state above. list/tuple: a "vec2"
                # argument's [x, y] pair (formgen.JoystickInput).
                stringified[key] = json.dumps(value)
            else:
                stringified[key] = str(value)
        return stringified

    @staticmethod
    def _validate_against_schema(schema: dict, args: dict, files: dict) -> None:
        """Catch obvious mistakes before paying a network round-trip.

        Mirrors the server's own checks (unexpected/missing arguments); a real
        request can still fail server-side (e.g. disallowed file extension).
        """
        tool_name = schema.get("name", "?")
        arguments = schema.get("arguments", {})

        for name in args:
            if name not in arguments:
                raise ServerToolError(f"Unexpected argument '{name}' for tool '{tool_name}'.")

        for name in files:
            if name not in arguments:
                raise ServerToolError(f"Unexpected file argument '{name}' for tool '{tool_name}'.")
            if not is_file_type(arguments[name].get("type", "")):
                raise ServerToolError(f"Argument '{name}' for tool '{tool_name}' is not a file argument.")

        for name, spec in arguments.items():
            if is_file_type(spec.get("type", "")):
                # A `server_selectable` file argument has two valid shapes: an
                # upload, or the NAME of a file the server hosts, sent as a
                # plain form value under the same field name. Requiring an
                # upload here would reject the second - the very shape that
                # keeps a hosted test cohort from travelling.
                satisfied = name in files or (spec.get("server_selectable") and name in args)
                if spec.get("required") and not satisfied:
                    raise ServerToolError(f"Missing required file argument '{name}' for tool '{tool_name}'.")
            elif spec.get("required") and name not in args:
                raise ServerToolError(f"Missing required argument '{name}' for tool '{tool_name}'.")
