"""Shared infrastructure for remote-server-backed Slicer tool modules.

Deliberately minimal: this module imports only `client`/`errors`/`config`, none
of which touch `slicer` or `qt`. That keeps `import ServerToolsCoreLib` safe
outside Slicer, which is what lets client.py be unit-tested in plain CI.

GUI-facing pieces (design, formgen, slicer_io, worker, base_widget) depend on
`slicer`/`qt`/`ctk` and must be imported explicitly by modules that run inside
Slicer, e.g. `from ServerToolsCoreLib.base_widget import ServerToolWidgetBase`.
"""

from . import config
from .client import (
    ToolResult,
    ToolServerClient,
    accepts_folder,
    argument_types,
    file_extensions_for,
    is_file_type,
    testfile_entries,
)
from .errors import ServerToolError

_client = None


def get_client() -> ToolServerClient:
    """Singleton client for the whole extension, so its /tools cache is shared."""
    global _client
    if _client is None:
        _client = ToolServerClient(
            server_url=config.SERVER_URL,
            token=config.API_TOKEN,
            verify_tls=config.VERIFY_TLS,
            timeout=config.TIMEOUT,
            parallelism=config.TRANSFER_PARALLELISM,
            chunk_bytes=config.TRANSFER_CHUNK_MB * 1024 * 1024,
            compress_uploads=config.TRANSFER_COMPRESS,
        )
    return _client


__all__ = [
    "get_client",
    "ToolServerClient",
    "ToolResult",
    "ServerToolError",
    "is_file_type",
    "argument_types",
    "accepts_folder",
    "file_extensions_for",
    "testfile_entries",
]
