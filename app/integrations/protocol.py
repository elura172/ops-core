from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)]
Amount = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$", max_length=13)]


class IntegrationError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    amount: Amount
    currency: Literal["CAD"]


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sku: Identifier
    quantity: Annotated[int, Field(strict=True, ge=1, le=1)]
    unit_price: Price

    @model_validator(mode="after")
    def positive_price(self) -> Line:
        if Decimal(self.unit_price.amount) <= 0:
            raise ValueError("a sellable copy needs a positive price")
        return self


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"]
    checkout_id: Identifier
    items: Annotated[list[Line], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def distinct(self) -> ReservationRequest:
        if len({line.sku for line in self.items}) != len(self.items):
            raise ValueError("each physical copy may appear only once")
        return self


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: Literal["checkout_abandoned", "payment_failed", "customer_cancelled"]


class OrderEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"]
    event_id: str
    event_type: Literal["order.paid", "order.cancelled"]
    occurred_at: str
    order_id: Identifier
    reservation_id: Identifier
    items: Annotated[list[Line], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def validate_event(self) -> OrderEvent:
        from datetime import datetime

        if str(UUID(self.event_id)) != self.event_id:
            raise ValueError("event_id must be a canonical UUID")
        when = datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        if len({line.sku for line in self.items}) != len(self.items):
            raise ValueError("each physical copy may appear only once")
        return self


@dataclass(frozen=True)
class SiteConnection:
    connection_id: str
    tenant_id: int
    inbound_key_id: str
    inbound_secret: str = field(repr=False)
    website_url: str = ""
    outbound_key_id: str = ""
    outbound_secret: str = field(default="", repr=False)
    enabled: bool = True
    checkout_enabled: bool = False
    delivery_enabled: bool = False
    stock_protection_enabled: bool = False
    channels: tuple[str, ...] = ()
    inbound_previous_keys: dict[str, str] = field(default_factory=dict, repr=False)


def parse_connections(raw: str) -> tuple[SiteConnection, ...]:
    parsed = json.loads(raw or "[]")
    if not isinstance(parsed, list):
        raise ValueError("CUSTOM_SITE_CONNECTIONS must be a JSON array")
    connections = []
    for value in parsed:
        if not isinstance(value, dict):
            raise ValueError("each custom site connection must be an object")
        value = dict(value)
        value["channels"] = tuple(value.get("channels", []))
        c = SiteConnection(**value)
        if (
            type(c.tenant_id) is not int
            or c.tenant_id < 1
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", c.connection_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", c.inbound_key_id)
            or len(c.inbound_secret.encode()) < 32
        ):
            raise ValueError("invalid custom site identity/signing configuration")
        for flag in (c.enabled, c.checkout_enabled, c.delivery_enabled, c.stock_protection_enabled):
            if type(flag) is not bool:
                raise ValueError("custom site switches must be JSON booleans")
        if c.delivery_enabled:
            url = urlsplit(c.website_url)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in ("", "/")
                or len(c.outbound_secret.encode()) < 32
                or c.inbound_secret == c.outbound_secret
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", c.outbound_key_id)
            ):
                raise ValueError("delivery requires an HTTPS origin and separate outbound signing key")
        if any(channel not in ("square", "abebooks", "biblio", "ebay") for channel in c.channels):
            raise ValueError("unsupported custom site stock-delivery channel")
        if not isinstance(c.inbound_previous_keys, dict):
            raise ValueError("previous signing keys must be a key-id to secret mapping")
        for key_id, secret in c.inbound_previous_keys.items():
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key_id)
                or not isinstance(secret, str)
                or len(secret.encode()) < 32
            ):
                raise ValueError("invalid previous inbound signing key")
        connections.append(c)
    if len({c.connection_id for c in connections}) != len(connections):
        raise ValueError("duplicate custom site connection ID")
    ids = [key for c in connections for key in (c.inbound_key_id, *c.inbound_previous_keys)]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate custom site inbound key ID")
    return tuple(connections)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def signature(secret: str, method: str, path: str, timestamp: str, event_id: str, body: bytes) -> str:
    prefix = "\n".join((method.upper(), path, timestamp, event_id)).encode() + b"\n"
    return "sha256=" + hmac.new(secret.encode(), prefix + body, hashlib.sha256).hexdigest()
