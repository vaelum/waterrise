"""waterrise: typed plans, `waterrise check`, and the local service. See GUIDE.md."""

# <year>.<month>.<n>: n counts the month's releases from 1; no zero padding.
__version__ = "2026.10.2"

# The oldest pin this waterrise still serves. A release that breaks what
# butler or description call (the command line, `status --json`) raises it to
# itself, and `install --check` then refuses older pins instead of running.
OLDEST_PIN = "2026.10.1"
