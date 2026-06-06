"""Fixtures for Loom tests.

The repo / repo_with_branches fixtures and make_stub come from the parent
tests/conftest.py (pytest collects parent conftests automatically). This file
adds a `gate_run` factory that builds a GateRun with sensible real-green
defaults so each test overrides only the field under test.
"""
import pytest


@pytest.fixture
def gate_run():
    from quilt.loom.audit import GateRun

    def make(**kw):
        defaults = dict(
            subject_id="mp1",
            gate="unit",
            tree_sha="t1",
            exit_code=0,
            stdout="5 passed",
            stderr="",
            expected_tests=5,
            coverage_paths=["a.c"],
            diff_paths=["a.c"],
        )
        defaults.update(kw)
        return GateRun(**defaults)

    return make
