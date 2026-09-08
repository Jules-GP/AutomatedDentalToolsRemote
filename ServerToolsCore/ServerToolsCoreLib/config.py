"""Static configuration for the tool server client.

Token and URL are constants here on purpose: this project settled on hard-coded
config over environment variables (see ARCHITECTURE.md). Do not commit a real
production token — override these locally, or replace them at build time.
"""

SERVER_URL = "http://localhost:8000"
API_TOKEN = "dev-token"
VERIFY_TLS = False
TIMEOUT = 600

# How many connections a single file's transfer uses at once (see transfer.py).
# This is what makes a remote server usable: one TCP stream over a long-haul
# link plateaus at a fraction of the available bandwidth because of its
# congestion window, and four streams multiply that by roughly four. On a LAN
# it changes little and costs nothing. Set to 1 to go back to one stream.
TRANSFER_PARALLELISM = 4

# How many runs of ONE tool may be in flight at once from a single panel.
# Apply always queues rather than refusing, so this only decides how much of the
# queue moves together.
#
# It is not a speed multiplier. The server serialises the card
# (MAX_CONCURRENT_GPU_JOBS = 1), so four segmentations still segment one at a
# time; what overlapping buys is the transfer of the next patient during the
# inference of the current one, which on a cohort of large CBCTs is most of the
# wall clock. It also lets a CPU tool run beside a GPU one.
#
# 1 is the default and the shape a panel is expected to have: ONE run of a tool
# at a time, extra clicks waiting their turn. Different tools are unaffected --
# each panel keeps its own queue, so AMASSS and ALI still run side by side.
#
# Raise it to overlap the transfer of the next patient with the inference of the
# current one, which on a cohort of large CBCTs is most of the wall clock. Keep
# it well under 4: the server admits MAX_CONCURRENT_TOOLS = 4 in total across
# every panel and every client, so spending them all from one panel would make
# another module's run wait behind this one's cohort.
CONCURRENT_RUNS = 1

# Size of one part, in megabytes. The server clamps this to [1, 64] and answers
# with what it actually used. Smaller parts recover faster from a dropped
# connection; larger ones amortise the per-request overhead.
TRANSFER_CHUNK_MB = 8

# Whether to gzip parts of files that are not already compressed (an
# uncompressed .nii, a .vtk mesh) on the way up. Roughly a third of the bytes,
# so roughly a third of the time on a remote link, for cheap level-1
# compression spread across the transfer threads. Already-compressed inputs
# (.nii.gz, .zip, ...) are never touched. Turn off on a fast LAN with a slow
# client machine, where the CPU is the scarcer resource.
TRANSFER_COMPRESS = True

# Whether to deflate a folder while packing it for upload. The archive exists
# because HTTP has no notion of a folder, not to make the data smaller, and
# the two are not the same trade: measured on this machine, packing STORED runs
# at 711 MB/s against 57 MB/s deflated, on ONE core. Sending 574 MB of raw .nii
# takes 1.5s uncompressed over a 379 MB/s link, and 10.8s if compressed first.
#
# Compressing only wins below roughly 27 MB/s (215 Mb/s). None decides from
# SERVER_URL -- a loopback or private address never wins -- and True/False
# forces it. Note this is the ARCHIVE; TRANSFER_COMPRESS is the wire, and the
# transfer layer never gzips a .zip, so this is the only place the choice is
# made for a folder argument.
ZIP_COMPRESS = None
