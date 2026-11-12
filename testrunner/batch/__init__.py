"""Batches: several render runs (references, compute, RT, ...) with one combined summary report."""

from .runner import run_batch

__all__ = ["run_batch"]
