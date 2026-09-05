"""Errors raised by the service layer.

These are meant to be shown to a user (or an AI agent) as-is, so the
messages should explain what went wrong in plain language.
"""


class AccountManagerError(Exception):
    """Base class for every error this project raises deliberately."""


class NotFound(AccountManagerError):
    pass


class PermissionDenied(AccountManagerError):
    pass


class InvalidAmount(AccountManagerError):
    pass


class InsufficientFunds(AccountManagerError):
    pass


class InvalidState(AccountManagerError):
    """The operation doesn't apply to the record in its current state."""
