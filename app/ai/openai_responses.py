from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.request import Request, urlopen

from app.ai.product_copy import CopyProvider, ProductCopy, ProductCopyRequest

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "claims": {"type": "array", "items": {"type": "string"}},
        "source_facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "claims", "source_facts"],
    "additionalProperties": False,
}


@dataclass
class OpenAIResponsesCopyProvider(CopyProvider):
    api_key: str
    model: str = "gpt-5"
    base_url: str = "https://api.openai.com/v1"
    timeout: float = 30

    def generate(self, request: ProductCopyRequest) -> ProductCopy:
        if not self.api_key:
            raise ValueError("api_key is required")
        facts = "\n".join(f"- {fact}" for fact in request.verified_facts)
        instructions = (
            "Write factual product copy. Use only verified facts. Do not invent "
            "benefits, certifications, brands, delivery promises, or health claims."
        )
        user_input = (
            f"Market: {request.market}\nProduct: {request.product_name}\nVerified facts:\n{facts}"
        )
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": user_input,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "product_copy",
                    "strict": True,
                    "schema": COPY_SCHEMA,
                }
            },
        }
        http_request = Request(
            f"{self.base_url.rstrip('/')}/responses",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(http_request, timeout=self.timeout) as response:
            if response.status >= 300:
                raise RuntimeError(f"OpenAI API returned HTTP {response.status}")
            payload = json.loads(response.read())
        output_text = payload.get("output_text")
        if not output_text:
            raise RuntimeError("OpenAI response did not contain output_text")
        try:
            result = json.loads(output_text)
            return ProductCopy(
                title=str(result["title"]),
                description=str(result["description"]),
                claims=tuple(map(str, result["claims"])),
                source_facts=tuple(map(str, result["source_facts"])),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("OpenAI response failed product-copy schema validation") from exc
