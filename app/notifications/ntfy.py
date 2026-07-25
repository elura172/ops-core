from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from app.domain.orders import SLAEvaluation
from app.observability.logging import get_logger

logger = get_logger("ntfy")

DEFAULT_SERVER = "https://ntfy.sh"


def send_ntfy(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    timeout: float = 5,
    *,
    server: str | None = None,
    token: str | None = None,
    allow_unauthenticated: bool = False,
) -> None:
    """Send an operational alert.

    On the public ntfy.sh server a topic is only as private as it is unguessable:
    anyone who knows or brute-forces it can read every alert. Order identifiers are
    operational data, so an unauthenticated send is refused unless the caller opts
    in explicitly. Supply ``token`` (an ntfy access token) or point ``server`` at a
    self-hosted instance.
    """
    if not topic or "/" in topic or " " in topic:
        raise ValueError("topic must be a non-empty single ntfy topic segment")
    resolved_server = (server or os.getenv("NTFY_SERVER") or DEFAULT_SERVER).rstrip("/")
    token = token or os.getenv("NTFY_TOKEN", "")

    if not token and not allow_unauthenticated:
        raise PermissionError(
            "refusing to publish alerts to an unauthenticated ntfy topic; set "
            "NTFY_TOKEN, use a self-hosted NTFY_SERVER, or pass "
            "allow_unauthenticated=True to accept that anyone with the topic can read them"
        )
    if not token:
        logger.warning("ntfy.unauthenticated", server=resolved_server)

    headers = {"Title": title, "Priority": priority, "Content-Type": "text/plain"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{resolved_server}/{topic}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status >= 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")


def format_sla_alert(evaluation: SLAEvaluation) -> str:
    return json.dumps(
        {
            "order_id": evaluation.order_id,
            "severity": evaluation.severity,
            "remaining_minutes": evaluation.remaining_minutes,
            "elapsed_ratio": evaluation.elapsed_ratio,
        }
    )
