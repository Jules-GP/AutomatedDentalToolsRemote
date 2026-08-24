"""The Slicer bridge, isolated: MRML export, zipping, and result loading.

Nothing here speaks HTTP. Every temporary file used by a tool module (an
exported volume, a zipped folder, a downloaded result) should go through
TempWorkspace so cleanup on error is never forgotten.
"""

import logging
import os
import re
import shutil
import tempfile
import zipfile
from typing import Optional
from urllib.parse import urlparse

import slicer

from . import config

logger = logging.getLogger("ServerToolsCore.slicer_io")


class TempWorkspace:
    """Context manager for a temp directory, removed on exit including on error."""

    def __init__(self, prefix="ServerTools_"):
        self._prefix = prefix
        self.path = None

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix=self._prefix)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.path and os.path.isdir(self.path):
            shutil.rmtree(self.path, ignore_errors=True)
        self.path = None
        return False

    def file(self, name: str) -> str:
        return os.path.join(self.path, name)


def export_node(node, dest_path: str) -> str:
    """Write a scene node out to `dest_path`, which is what says its format.

    One function for volumes and surfaces alike: `saveNode` picks its writer
    from the extension, and the caller already had to choose one.
    """
    ok = slicer.util.saveNode(node, dest_path)
    if not ok:
        raise IOError(f"Failed to export {node.GetName()} to {dest_path}")
    return dest_path


def export_volume(volume_node, dest_path: str) -> str:
    """Kept as its own name: it is what the modules already call."""
    return export_node(volume_node, dest_path)


def is_extractable_archive(path: str) -> bool:
    """Whether `path` should be unpacked as a delivery archive.

    Deliberately extension-based, not `zipfile.is_zipfile()`: OOXML formats
    (.xlsx, .docx, .ods, .pptx, ...) are zip containers structurally, so a
    signature check would "extract" a result .xlsx into its raw XML parts
    instead of keeping it as the file it actually is. Only a genuine `.zip`
    is meant to be unpacked here.
    """
    return path.lower().endswith(".zip")


# Extensions whose bytes are already compressed. DEFLATE gains ~0% on them and
# runs at ~45 MB/s on one core, so a folder of .nii.gz scans used to spend
# seconds per 100 MB shrinking nothing -- measured 2.3s to pack 105 MB of
# gzipped CBCT into an archive of exactly the same 105 MB, before a single byte
# was sent, and the server paid it again inflating them. `.gz` covers the
# compound medical extensions (.nii.gz, .nrrd.gz, .gipl.gz); the OOXML formats
# are zip containers by design. Mirrors the server's own table in
# file_utils.py.
_STORED_EXTENSIONS = (
    ".gz", ".bz2", ".xz", ".zip", ".7z",
    ".xlsx", ".ods", ".docx", ".pptx",
    ".png", ".jpg", ".jpeg",
)

# Level 1 for everything else: it compresses at roughly twice the rate of the
# default 6 and gives up about 3% of size on the one kind of member still worth
# deflating here (binary .vtk, ~2.7:1 at either level).
_COMPRESS_LEVEL = 1


# Hosts whose link is faster than the compressor, so deflating on the way out
# costs more than it saves (see config.ZIP_COMPRESS for the measurements).
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _link_is_fast(server_url: str) -> bool:
    """Is the server close enough that compressing is a net loss?

    Loopback and the private ranges only. Anything else is treated as remote,
    which is the safe way round: guessing "fast" on a real link would spend a
    third more bytes on the wire to save CPU that was not the constraint.
    """
    host = urlparse(server_url).hostname or ""
    if host in _LOCAL_HOSTS:
        return True
    return host.startswith(("10.", "192.168.")) or bool(
        re.match(r"172\.(1[6-9]|2[0-9]|3[01])\.", host)
    )


def zip_folder(folder: str, dest_path: str, compress: Optional[bool] = None) -> str:
    """Pack a folder for upload, choosing the compression per member.

    A folder argument is zipped only because HTTP has no notion of a folder --
    the archive is a container, not an attempt to make the data smaller. So
    already-compressed members are STORED as-is and only what genuinely
    deflates is deflated, which is 14x faster to pack for exactly the same
    bytes on the wire.

    `compress` decides whether the rest is deflated at all. None reads
    config.ZIP_COMPRESS, which in turn defaults to "not against a local
    server": deflating runs at 57 MB/s on one core, and a link faster than
    about 27 MB/s carries the raw bytes sooner than the compressor can shrink
    them.
    """
    if not os.path.isdir(folder):
        raise IOError(f"Not a folder: {folder}")
    if compress is None:
        compress = config.ZIP_COMPRESS
    if compress is None:
        compress = not _link_is_fast(config.SERVER_URL)
    default_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(
        dest_path, "w", default_type, compresslevel=_COMPRESS_LEVEL
    ) as archive:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                full_path = os.path.join(root, name)
                # compress_type=None defers to the archive's default (DEFLATED
                # at _COMPRESS_LEVEL); already-compressed members opt out.
                stored = name.lower().endswith(_STORED_EXTENSIONS)
                archive.write(
                    full_path,
                    os.path.relpath(full_path, folder),
                    compress_type=zipfile.ZIP_STORED if stored else None,
                )
    return dest_path


def unzip_folder(zip_path: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(dest_dir)
    return dest_dir


_LOADERS = {
    "segmentation": lambda path: slicer.util.loadSegmentation(path),
    # Labelled VOXELS, kept as voxels. A segmentation node builds a closed
    # surface representation to show itself in 3D, so a labelmap loaded as one
    # arrives as triangles - an appearance the tool never produced. AMASSS
    # writes a labelled grid and generates no surface unless asked
    # (generate_surface defaults to False), so the mesh was the panel's doing,
    # not the tool's. The caller can still build a surface in Slicer when that
    # is what they want.
    "labelmap": lambda path: slicer.util.loadLabelVolume(path),
    "volume": lambda path: slicer.util.loadVolume(path),
    "model": lambda path: slicer.util.loadModel(path),
    "transform": lambda path: slicer.util.loadTransform(path),
}


def load_result(path: str, kind: str):
    loader = _LOADERS.get(kind)
    if loader is None:
        raise ValueError(f"No MRML loader registered for result kind '{kind}'.")
    return loader(path)
