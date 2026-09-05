"""Optional AI features.

Everything in this package is optional. The rest of the project works
completely without it, and the anomaly detector works without any AI
provider at all.
"""

from .base import AIError, AIProvider, AIUnavailable
from .providers import available_providers, get_provider

__all__ = [
    "AIError",
    "AIProvider",
    "AIUnavailable",
    "available_providers",
    "get_provider",
]
