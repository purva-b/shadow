"""Privacy-boundary unit tests.

The leak (brief #2) was that the restricted-topic detector RETURNED the
restricted file's title/topic and attendees to the asker. These tests assert
the detector now returns only boolean signals — nothing about the file crosses
the boundary — without needing a live Box call.
"""
from __future__ import annotations

import inspect


def test_restricted_hit_returns_no_file_metadata():
    """_restricted_hit must never return file name, title, topic, or attendees —
    only the boolean `only` flag. Verified structurally against the source so it
    holds without a live Box tenant."""
    from agents import answer

    src = inspect.getsource(answer._restricted_hit)
    # the only dict this function may return is {"only": ...}
    assert '"file_name"' not in src, "leak: returns file_name"
    assert '"attendees"' not in src, "leak: returns attendees"
    assert '"topic"' not in src, "leak: returns topic"
    assert 'return {"only":' in src, "must return only the boolean signal"


def test_restricted_who_takes_no_attendees():
    """The who-to-ask for a restricted topic must be derived from readable
    people cards, not from the restricted file's attendee list. The refactored
    _restricted_who takes no attendees argument."""
    from agents import answer

    src = inspect.getsource(answer._answer_pipeline)
    # the leaky call signature _restricted_who(restricted.get("attendees"...)) is gone
    assert '_restricted_who(restricted.get("attendees"' not in src
    assert "_restricted_who()" in src


def test_find_restricted_topic_tool_returns_no_metadata():
    """The Strands tool form of the check must also leak nothing."""
    from agents import tools_strands

    src = inspect.getsource(tools_strands.find_restricted_topic)
    assert '"topic":' not in src, "tool leaks topic"
    assert '"attendees":' not in src, "tool leaks attendees"
