# Ops-Core High-Volume Scale Crucible Report

*Generated on: 2026-10-22 22:13:41 UTC*

## Executive Summary

The **Scale Crucible** subjected Ops-Core to a comprehensive operational stress test under a realistic **10,000 SKU inventory load** (75% standard trade used, 20% collectible literature, 5% high-value rare antiquarian) and **500 active collector want requests**.

The benchmark verified the core architectural contracts of Ops-Core:
1. **Zero Double-Selling Under High Concurrency**: 100 simultaneous 3-channel contention races resolved with 100% atomic arbitration and 0 double-sells.
2. **Sub-50ms Search SLA**: Faceted inventory queries, shelf/section lookups, and SKU accesses comfortably beat the 50ms SLA.
3. **Dual Engine Parity**: Both local SQLite (single-store workstation default) and durable PostgreSQL (multi-tenant cloud production) proved robust and performant.

---

## Performance Scorecard (10,000 SKUs)

| Metric | SQLite (Local Edge) | PostgreSQL (Multi-Tenant Production) | Target SLA / Goal | Status |
|---|---|---|---|---|
| **100-Book Batch Ingest Time** | 0.078s | 0.126s | < 2.0s | PASS |
| **Batch Ingest Rate** | 1278.9 bks/s | 794.6 bks/s | > 50 bks/sec | PASS |
| **Search Latency (p50)** | 5.10 ms | 3.22 ms | < 10 ms | PASS |
| **Search Latency (p95)** | 8.86 ms | 10.40 ms | < 50 ms | PASS |
| **Search Latency (p99)** | 15.02 ms | 20.06 ms | < 100 ms | PASS |
| **500 Want Matching Time** | 0.105s | 0.109s | < 1.0s | PASS |
| **Concurrent Contention Races** | 100 races | 100 races | 100 concurrent races | PASS |
| **Double-Sell Violations** | **0** | **0** | **0 (Strict Invariant)** | **VERIFIED** |

---

## Four Stress Vectors Tested

### Vector 1: Rapid Bulk Ingestion
Simulated an estate buyout intake of 100 volumes across multiple shelves with full bibliographic metadata, pricing, condition attributes, and collation notes.

### Vector 2: Search & Filter Latency
Executed 500 randomized faceted queries across the 10,000-book catalog (SKU lookups, shelf audits, section filters, price ranges). Guaranteed sub-50ms p95 latency for interactive bookseller browsing.

### Vector 3: Collector Want List & Atomic Holds
Evaluated newly cataloged rare inventory against 500 active collector want requests. Verified that matching volumes generated atomic reservation hold slips with 48-hour expirations.

### Vector 4: Multi-Channel Order Contention
Dispatched 100 simultaneous 3-channel collisions (AbeBooks vs. eBay vs. Amazon) within millisecond windows. Verified that the `OrderContentionManager` and SLA-priority arbiter allocated exactly one winner per unique physical book while cleanly initiating cancellation/arbitration for losing orders.
