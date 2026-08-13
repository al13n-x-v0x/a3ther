"""
actions/send_message.py — MESSAGE intent.

Sending messages to external channels (WhatsApp / Telegram / SMS / …)
requires a connected channel provider. This module reports honestly when no
provider is configured rather than faking a send.
"""

from __future__ import annotations

from typing import Any, Dict


def send_message(params: Dict[str, Any] | None = None) -> str:
    params = params or {}
    recipient = params.get("recipient") or params.get("to") or params.get("contact") or ""
    text = params.get("text") or params.get("message") or ""
    channel = params.get("channel") or "messaging"
    if recipient:
        return (
            f"{channel.capitalize()} is not connected to a provider yet — "
            f"no message was sent to {recipient}. Configure a channel in Settings to enable sending."
        )
    return (
        f"{channel.capitalize()} is not connected — configure a channel provider "
        "in Settings before sending messages."
    )


if __name__ == "__main__":  # pragma: no cover
    print(send_message({"recipient": "Shellified", "text": "hi"}))
