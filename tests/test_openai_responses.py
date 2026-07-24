import json

import pytest

from app.ai.openai_responses import OpenAIResponsesCopyProvider


def test_openai_provider_requires_key():
    with pytest.raises(ValueError):
        OpenAIResponsesCopyProvider("").generate(None)


def test_schema_is_included_in_provider_request(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def read(self):
            return json.dumps(
                {
                    "output_text": json.dumps(
                        {
                            "title": "Box",
                            "description": "Glass",
                            "claims": [],
                            "source_facts": ["Glass"],
                        }
                    )
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr("app.ai.openai_responses.urlopen", fake_urlopen)
    from app.ai.product_copy import ProductCopyRequest

    result = OpenAIResponsesCopyProvider("key").generate(
        ProductCopyRequest("Box", ("Glass",), "GB")
    )
    assert result.title == "Box"
    assert captured["body"]["text"]["format"]["type"] == "json_schema"
