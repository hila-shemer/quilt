"""Loom — the rebase-based orchestration layer on top of quilt.

This package is additive: it imports and reuses quilt's primitives (gitio, db,
gates, llm, resolve, candidate, backprop, scheduler) but does not modify them.
See docs/plans/2026-06-06-loom-00-master.md for the build order.
"""

__version__ = "0.1.0"
