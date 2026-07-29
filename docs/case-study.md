# Case study: a system that documented five safety guarantees and enforced none

**Codebase:** a Python/FastAPI operations service for an authorized TikTok Shop
integration — order webhooks, a human approval queue for anything that mutates
the shop, and adapters held behind explicit gates.

**Starting state:** 64 tests, all passing. Clean architecture, typed dataclasses,
protocols at every seam. It looked healthy.

**The problem:** its README stated five compliance guarantees. The code enforced
none of them, and the passing test suite covered precisely the properties that
were not true.

---

## What the audit found

Eight defects, each verified by reproduction before being reported. The four that
mattered:

### 1. Order webhooks lost data silently

`handle_order_webhook` returned its errors as a dictionary, so FastAPI
serialised them with **HTTP 200**. Webhook delivery is not ordered, so an event
arriving out of sequence raised, was caught, and returned success — the platform
recorded a successful delivery and never retried. The order update was gone.

There was no logging anywhere in the application, so nothing recorded the loss.

*Verified:* posted an out-of-order event, received `200`, confirmed the state was
never written.

### 2. The human-approval control had no authentication

The approval endpoints — the entire human-in-the-loop safety mechanism — required
no credentials. The reviewer's identity was a **string in the request body**.

*Verified:* `POST /reviews/{id}/decision {"approve": true, "reviewer": "attacker"}`
returned `APPROVED`, attributed to "attacker".

### 3. Approved actions were infinitely replayable

Approval never reached a terminal state, so `execute` could be called repeatedly,
re-running the same mutation each time.

*Verified:* called it twice, got two identical successful executions.

### 4. The safety gates were decorative

`LaunchGate` and `PublishGate` — the controls preventing live mutation until an
operational checklist was attested — appeared **only inside tests**. Neither was
on any call path. `DRY_RUN` was read only by a config class nothing imported.

Also found: a non-atomic check-then-write in the SQLite repository (masked by the
single-threaded handler, live the moment a second worker existed), and an A/B
assignment hash that made concurrent experiments perfectly correlated rather than
independent.

---

## What changed

| | Before | After |
|---|---:|---:|
| Tests | 64 | 273 |
| Type checking | none | `mypy --strict`, clean across 72 files |
| CI | none | lint, format, types, tests, container build |
| Logging | none | structured JSON with correlation IDs |

Each guarantee was made real and proven:

- **Webhook correctness** — events classified against stored state instead of
  assumed adjacency; real status codes; conflicts durably quarantined and exposed
  for human resolution; storage failures return `500` so the platform retries.
- **Authentication** — identity from a bearer token, never the request body;
  roles separating approval from execution; an unconfigured token registry
  answers `503` rather than serving open.
- **Execute-once, durably** — approval claimed with a conditional
  `UPDATE ... WHERE status = 'APPROVED'` inside a transaction. *Verified: six
  threads racing one approval produced exactly one call to the client; a restart
  replayed the stored result instead of re-running.*
- **Launch gates wired** — the checklist now runs before a live client can be
  constructed, so an unattested deployment fails to boot rather than at its first
  mutation. Two of its controls are derived from configuration and cannot be
  self-declared.

Type checking found two real bugs behind the annotation noise: a method named
`list` shadowing the builtin inside its own return annotations, and a variable
bound to both a store and a route handler.

---

## How the work was checked

Three findings came from auditing my own output rather than the original code:

- The execute-once guarantee was first built on an in-process dictionary — the
  same class of flaw the audit had just criticised. Moved to SQLite before it
  shipped.
- A CI step asserting that an unattested deployment fails to boot would have
  passed with the gate deleted, because it omitted a credential and failed for an
  unrelated reason. Rewritten to fail for the right reason.
- A token budget was documented as a hard cap; measurement showed it could
  overshoot by one call, because a request's cost is unknown until its response
  arrives. Documented and pinned with a test named after the limitation.

Every commit records what was **not** verified. The container image was never
built and CI never ran — no Docker daemon was available — so the commit says so
rather than implying otherwise.

---

## Method

The audit is AI-accelerated and adversarially checked: findings are reproduced
before being reported, and claims about severity are qualified by whether a
defect is live or currently masked. That distinction matters — reporting a masked
race as an active bug is how audits lose credibility.

Roughly two days of work, start to finish.

---

## What this is not

This was my own codebase, audited by me. It is a demonstration of method on real
code with real defects, not a client engagement. There are no third-party
references behind it yet.

The full history is public and every claim above is checkable against the
commits: **github.com/elura172/ops-core** — branch `harden/controls-and-correctness`.
