"""Public in-process task aggregation and aircraft allocation interface.

Legacy components remain importable from their individual submodules.
"""

from .collaboration_v1 import aggregate_and_allocate

__all__ = ["aggregate_and_allocate"]
