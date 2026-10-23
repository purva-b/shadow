"""Concrete notification delivery — makes `notify.register_sender` real.

`core/notify.py` detects notable records and targets principals; it stays
delivery-agnostic. This module provides an actual sender and registers it at
startup, so a detected notification is really delivered instead of only landing
in the local feed.

Delivery is chosen by env, honest-degradation style:
  * SHADOW_SLACK_WEBHOOK_URL set → POST the notification to that Slack Incoming
    Webhook (the simplest real channel; no per-user token needed).
  * otherwise → a logging sender that prints the notification, so the hook is
    always wired and observable in dev rather than silently doing nothing.

A future per-user Slack DM sender can replace this by reading the recipient's
connector token from core.token_store; the seam (register_sender) is unchanged.
"""
from __future__ import annotations

import os

from . import notify


def _slack_webhook_sender(url: str):
    def send(principal: str, title: str, body: str) -> None:
        import requests

        requests.post(url, json={"text": f"*{title}*\n{body}\n_to: {principal}_"},
                      timeout=10)
    return send


def _logging_sender(principal: str, title: str, body: str) -> None:
    print(f"[notify→{principal}] {title} — {body}", flush=True)


def install() -> str:
    """Register the best available sender with core.notify. Returns which one,
    so startup can log it. Idempotent."""
    url = os.environ.get("SHADOW_SLACK_WEBHOOK_URL", "").strip()
    if url:
        notify.register_sender(_slack_webhook_sender(url))
        return "slack-webhook"
    notify.register_sender(_logging_sender)
    return "logging"
