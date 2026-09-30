"""Which patients a reader asked to have done again, and where that is kept.

The one thing a reviewer produces that is not a corrected file: a list. Six
cases look fine, two do not, and what happens next -- re-running those two,
handing them to someone else, coming back tomorrow -- depends on the list
surviving the session.

**It is written beside the data, not into a preference store.** A mark is
about the cohort, not about the person who made it: whoever opens the folder
next should see what was already judged, including on another machine. A
QSettings key would have made two readers of one folder disagree silently.

The file is ours and its name says so. It is never confused with a tool's
output: `index` skips it like every other report.
"""

import json
import os

FILENAME = "visu-review.json"

# The shape written. A version, because the next thing this file wants to
# carry is a note per patient, and a reader that predates that must not
# choke on it.
VERSION = 2

# What the list is called on disk. It was `flagged` through version 1, and a
# file written then is still read: the word changed, the meaning never did.
# "Flag" said that something had been noticed and not what would happen to it,
# while every layer underneath -- `REPLAY_DIRNAME`, `narrow_to_cases`,
# "Replaying %s over %d of its cases" -- already called it a replay. One word
# across the whole stack, and it is the one that names the consequence.
MARKED = "replay"
_MARKED_V1 = "flagged"


def path_for(folder: str) -> str:
    return os.path.join(folder, FILENAME)


def load(folder: str) -> set:
    """The case keys marked for a replay, or an empty set for a fresh folder.

    Reads the version-1 spelling too. A clinician who marked eight patients
    yesterday opens the same folder today, and losing that list to a rename
    would be the rename's fault, not theirs.
    """
    try:
        with open(path_for(folder), encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        # A folder nobody has reviewed, one on a read-only mount, or a file
        # something else wrote. None of the three is an error here: the
        # reader starts with nothing marked.
        return set()
    marked = document.get(MARKED)
    if not isinstance(marked, list):
        marked = document.get(_MARKED_V1)
    return {str(key) for key in marked} if isinstance(marked, list) else set()


def save(folder: str, marked) -> bool:
    """Write the marks beside the data. False when the folder will not take it.

    Not raising: a hosted sample is unpacked into a temporary directory that
    is deleted on the next download, and a cohort on a read-only share is
    perfectly normal. Losing the marks is worth a line in the panel, never a
    dialog over a scan.
    """
    document = {"version": VERSION, MARKED: sorted(marked)}
    staging = path_for(folder) + ".visu-tmp"
    try:
        with open(staging, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
        os.replace(staging, path_for(folder))
    except OSError:
        try:
            os.remove(staging)
        except OSError:
            pass
        return False
    return True


def as_text(marked, folder: str = "") -> str:
    """The list as something to paste into a message or a ticket."""
    marked = sorted(marked)
    if not marked:
        return "Nothing marked to replay."
    head = f"{len(marked)} to replay"
    if folder:
        head += f" in {folder}"
    return "\n".join([head + ":"] + [f"  {key}" for key in marked])
