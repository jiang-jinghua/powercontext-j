"""Errors surfaced by the SDD context module."""

from __future__ import annotations


class SddError(RuntimeError):
    """Base class for failures that should be reported to the operator."""


class ConfigurationError(SddError):
    """The environment or a config value is unusable."""


class StoreError(SddError):
    """The seekdb/OceanBase corpus store failed."""


class RetrievalError(SddError):
    """Context retrieval could not produce a usable pack."""


class ModelError(SddError):
    """The MiniMax model call failed."""


class PatchError(SddError):
    """The model output is not an applicable unified diff."""
