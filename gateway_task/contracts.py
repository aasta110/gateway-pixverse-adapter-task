"""Stable boundary between a provider adapter and the supplied runner."""

from typing import Literal


PollResult = tuple[
    Literal["processing", "done", "error"],
    str | None,
    str | None,
    dict | None,
]


class RelayError(Exception):
    """An adapter failure with a message safe to return to a gateway caller.

    ``status`` is the intended gateway HTTP status. ``upstream_status`` is the
    provider HTTP status, when available. The adapter must construct a safe
    message: this class does not sanitize raw provider responses or secrets.
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.upstream_status = upstream_status
