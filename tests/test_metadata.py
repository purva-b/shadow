"""apply_metadata patch-op logic (brief #12).

The bug: on an update, mixing NEW and EXISTING fields failed — all-add broke on
the existing field, all-replace broke on the new one. _patch_ops chooses the op
per field, so the mixed case works. Pure logic, no live Box.
"""
from __future__ import annotations

from shadow_box.tools import _patch_ops


def test_all_new_fields_use_add():
    ops = _patch_ops({"a": 1, "b": 2}, existing={})
    assert all(o["op"] == "add" for o in ops)


def test_all_existing_fields_use_replace():
    ops = _patch_ops({"a": 1}, existing={"a": 0})
    assert ops == [{"op": "replace", "path": "/a", "value": 1}]


def test_mixed_new_and_existing_is_the_bug_that_is_fixed():
    """The case that broke before: 'a' exists, 'b' is new."""
    ops = _patch_ops({"a": 9, "b": 7}, existing={"a": 1})
    by_path = {o["path"]: o["op"] for o in ops}
    assert by_path == {"/a": "replace", "/b": "add"}
