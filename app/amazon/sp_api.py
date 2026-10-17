"""Small, fail-closed client for Amazon's Selling Partner API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.net import RetryPolicy

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"


class AmazonSPAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class LwaTokenProvider:
    client_id: str
    client_secret: str
    refresh_token: str
    endpoint: str = LWA_TOKEN_URL
    timeout: float = 15
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    _access_token: str = field(default="", init=False, repr=False)
    _expires_at: float = field(default=0, init=False, repr=False)

    def get_access_token(self) -> str:
        if self._access_token and time.monotonic() < self._expires_at - 60:
            return self._access_token
        if not all((self.client_id, self.client_secret, self.refresh_token)):
            raise AmazonSPAPIError("Amazon SP-API LWA credentials are incomplete")

        body = urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        ).encode()
        request = Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        for attempt in range(1, self.retries.max_attempts + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise AmazonSPAPIError("Amazon LWA returned an invalid token response")
                token = result.get("access_token")
                expires_in = int(result.get("expires_in", 3600))
                if not token or expires_in <= 0:
                    raise AmazonSPAPIError("Amazon LWA returned an invalid token response")
                self._access_token = str(token)
                self._expires_at = time.monotonic() + expires_in
                return self._access_token
            except HTTPError as exc:
                retryable = exc.code in (429, 500, 502, 503, 504)
                if not retryable or attempt >= self.retries.max_attempts:
                    raise AmazonSPAPIError(
                        f"Amazon LWA token request failed (HTTP {exc.code})",
                        status_code=exc.code,
                    ) from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt >= self.retries.max_attempts:
                    raise AmazonSPAPIError("Amazon LWA token request failed") from exc
            time.sleep(self.retries.backoff(attempt))
        raise AmazonSPAPIError("Amazon LWA token request exhausted retries")


@dataclass
class AmazonSPAPIClient:
    token_provider: LwaTokenProvider
    endpoint: str = "https://sellingpartnerapi-na.amazon.com"
    app_name: str = "OpsCore"
    app_version: str = "0.1"
    timeout: float = 20

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str | list[str]] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from urllib.parse import urlencode as encode_query

        url = self.endpoint.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + encode_query(query, doseq=True)
        headers = {
            "x-amz-access-token": self.token_provider.get_access_token(),
            "x-amz-date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "user-agent": f"{self.app_name}/{self.app_version} (Language=Python)",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
            result = json.loads(raw) if raw else {}
            if not isinstance(result, dict):
                raise AmazonSPAPIError("Amazon SP-API returned an invalid response")
            return result
        except HTTPError as exc:
            raise AmazonSPAPIError(f"Amazon SP-API request failed (HTTP {exc.code})", status_code=exc.code) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AmazonSPAPIError("Amazon SP-API request failed") from exc

    def get_offer_definition(self, marketplace_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            "/definitions/2020-09-01/productTypes/PRODUCT",
            query={
                "marketplaceIds": marketplace_id,
                "requirements": "LISTING_OFFER_ONLY",
                "requirementsEnforced": "ENFORCED",
                "locale": "en_CA",
                "parentageLevel": "NONE",
            },
        )

    def get_listing(self, seller_id: str, sku: str, marketplace_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/listings/2021-08-01/items/{seller_id}/{sku}",
            query={
                "marketplaceIds": marketplace_id,
                "includedData": ["summaries", "issues", "offers", "fulfillmentAvailability"],
                "issueLocale": "en_CA",
            },
        )

    def submit_listing(
        self, seller_id: str, sku: str, marketplace_id: str, attributes: dict[str, Any]
    ) -> dict[str, Any]:
        return self.request(
            "PUT",
            f"/listings/2021-08-01/items/{seller_id}/{sku}",
            query={"marketplaceIds": marketplace_id},
            body={
                "productType": "PRODUCT",
                "requirements": "LISTING_OFFER_ONLY",
                "attributes": attributes,
            },
        )

    def set_offer_quantity(self, seller_id: str, sku: str, marketplace_id: str, quantity: int) -> dict[str, Any]:
        if quantity < 0:
            raise ValueError("Amazon offer quantity cannot be negative")
        return self.request(
            "PATCH",
            f"/listings/2021-08-01/items/{seller_id}/{sku}",
            query={"marketplaceIds": marketplace_id},
            body={
                "productType": "PRODUCT",
                "patches": [
                    {
                        "op": "replace",
                        "path": "/attributes/fulfillment_availability",
                        "value": [
                            {
                                "fulfillment_channel_code": "DEFAULT",
                                "quantity": quantity,
                            }
                        ],
                    }
                ],
            },
        )
