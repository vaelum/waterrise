"""The one way waterrise reports failure, as the harness's ButlerError does.

Helpers raise; `__main__.main` is the only place that turns a failure into an
exit code and a message.
"""

from __future__ import annotations


class WaterriseError(Exception):
    """A failure with a message meant for the user, not a traceback.

    `hint` is printed as an indented follow-up line: the command that fixes
    the problem, which is almost always what the user wants next.
    """

    def __init__(self, message: str, *, hint: str | None = None, code: int = 1):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
