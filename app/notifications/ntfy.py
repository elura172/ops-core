from __future__ import annotations

import json
from urllib.request import Request, urlopen


def send_ntfy(topic: str, title: str, message: str, priority: str = "default", timeout: float = 5) -> None:
    """Send an alert. Caller must provide a non-empty, intentionally chosen topic."""
    if not topic or "/" in topic or " " in topic:
        raise ValueError("topic must be a non-empty single ntfy topic segment")
    request = Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Content-Type": "text/plain"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status >= 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")


def format_sla_alert(evaluation) -> str:
    return json.dumps({
        "order_id": evaluation.order_id,
        "severity": evaluation.severity,
        "remaining_minutes": evaluation.remaining_minutes,
        "elapsed_ratio": evaluation.elapsed_ratio,
    })
