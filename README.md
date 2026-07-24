# TikTok Ops MVP

Dry-run-first operations core for an authorized TikTok Shop integration.

## Safety defaults

- No proxy rotation, fingerprint spoofing, stealth browser automation, or scraping bypasses.
- No live API mutations until the official sandbox flow and request signing are implemented and tested.
- No buyer PII in logs or trend datasets.
- Product creation and fulfillment mutations require explicit application-level approval.
- Browser automation is disabled by default; any UI adapter must be human-supervised and separately approved.

## Run tests

```bash
python -m pytest
```

The `TikTokClient` is deliberately a boundary rather than a fake implementation. Current API versions, scopes, signatures, categories, warehouse IDs, and shipping providers must be obtained from TikTok Shop Partner Center and tested in its sandbox before enabling live calls.
