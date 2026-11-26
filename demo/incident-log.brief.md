# BRIEF 401b067674

## 1. REQUEST (verbatim, never compressed)

Where did this start, what did we decide and why, which tests passed and which failed, and what is still open?

## 2. CONSTRAINTS AND REQUIREMENTS (verbatim from the source)

- (L13) Result: staging never goes above 240ms p95. So the problem does not reproduce
- (L31) re-enable pooling with max_idle=8 per host. Must keep max_open=40, because the
- (L32) old incident came from 400 open connections per pod. Do not repeat that.
- (L57) no rollback plan if v3 misses keys. Do not ship it to 100% without a canary.
- (L59) Open question nobody answered: why did p95 jump exactly at the release, if the
- (L60) real cause is Redis eviction plus pool exhaustion? The release only changed the
- (L70) canary at 1%, do not expand while key_miss is unexplained. Do not disable the
- (L104) and cap the list at 5,000 entries per tenant. Must be applied with a script that
- (L106) never a single `DEL` on the whole prefix (that stalls the cluster, we did it once
- (L111) 10% — do not go to 100% until the readiness timeout change has survived a week.

## 3. THE THREAD (chronological — including what failed)

1. Investigate checkout latency regression after 2026-02-11 release; first theory blamed retry wrapper on non-idempotent POSTs.
   - why: p95 went 180ms to 2.4s, payment-spinner tickets doubled
   - evidence: “We started this because checkout latency went from 180ms p95 to 2.4s p95 after the 2026-02-11 release, and sup” [L3-6]
2. Parked retry theory after Ahmed's traces showed slow spans inside tokenservice.validate.
   - why: max span 9.1s was in validate, not retry path
   - evidence: “Ahmed disagreed. His reading of the traces: the slow spans are all inside `tokenservice.validate`, not in the ” [L8-10]
3. Ran k6 load test on staging; could not reproduce.
   - why: killed the 'slow everywhere' theory
   - evidence: “19 Feb — built a synthetic load test (k6, 300 rps, 10 min) against staging. Result: staging never goes above 2” [L12-15]
4. Sampled prod spans, found cache-b at 96% memory; raised maxmemory 4gb to 6gb.
   - why: eviction storms would explain 9s validate spans
   - evidence: “20 Feb — turned on span sampling in production for 1% of traffic, 6 hours. Found that 71% of slow requests hit” [L17-25]
5. Re-enabled connection pooling with max_idle=8 per host, keeping max_open=40.
   - why: pool disabled in January; single AZ eu-central-1c affected; avoid old 400-connection incident
   - evidence: “22 Feb — Sofia noticed that the remaining slow requests all come from a single AZ (eu-central-1c). Cross-AZ la” [L27-36]
6. Rolled pooling to 10% of prod, hit 3.4% 502s, rolled back in 11 minutes; raised readiness timeout to 5s.
   - why: 1s probe timeout pulled healthy pods from load balancer
   - evidence: “24 Feb — rolled pooling to 10% of production. p95 610ms. Then at 14:20 the error rate jumped to 3.4% with 502s” [L38-43]
7. Retried pooling at 10% with 5s readiness; p95 600ms, asked platform team for dedicated connection proxy.
   - why: still above the 400ms target, 6gb on cache-b not enough
   - evidence: “25 Feb — readiness timeout raised, pooling at 10% again, no 502s for 6 hours. p95 600ms. Sent the numbers to t” [L45-48]
8. Decided to replace 220kb registry parsing with compressed keys:v3 lookup (14kb); owner self, deadline 3 March.
   - why: JSON serialization cost 340ms CPU per slow request
   - evidence: “26 Feb — target was p95 < 400ms. We are at 600ms. Remaining hot spot per profiler: JSON serialization in valid” [L50-57]
9. Held v3 canary at 1% due to unexplained key_miss on acme-eu; kept fallback enabled.
   - why: unknown whether tenants lack v3 entries or exporter dropped them
   - evidence: “27 Feb — v3 lookup behind a flag on 1% of prod. Canary numbers: p95 430ms, errors 0.02% (same as baseline), bu” [L65-71]
10. Found retry:queue:* at 3.1M entries; added 600s TTL and 5,000-entry cap via batched script; p95 380ms.
   - why: untrimmed retry bookkeeping pushed cache-b into eviction
   - evidence: “28 Feb — checked key counts. `retry:queue:*` holds 3.1 million entries, oldest from 2026-02-02. So Ahmed was r” [L98-113]
11. Expanded v3 to 25% and assigned 4-tenant backfill to Marko by 6 March.
   - why: exporter skips tenants written before v3 migration, not data loss
   - evidence: “2 March — Marko is back. He confirmed the exporter skips tenants whose registry was written before the v3 migr” [L115-121]
12. Kept pooling at 10%, readiness 5s, TTL cron 6h; recorded three failed attempts (8gb memory, disabling retry wrapper, read replica).
   - why: 8gb no effect, disabling retries lost 0.4% payments, replica lag gave stale keys
   - evidence: “5 March — The dedicated connection proxy got approved by platform; it lands next sprint, no date yet. In the m” [L127-134]

## 4. FACTS, NUMBERS, NAMES

- p95 180ms before 2026-02-11 release, 2.4s after; tickets 412 vs 205
- max span in tokenservice.validate 9.1s
- k6 load test: 300 rps, 10 min, staging p95 never above 240ms
- 71% of slow requests on cache-b, 96% memory used; maxmemory 4gb to 6gb
- After resize p95 640ms; target p95 < 400ms
- Single AZ eu-central-1c; max_idle=8 per host, max_open=40
- Staging pooling: 1 failed request of 180,000, ECONNRESET, not reproducible in 3 runs
- Prod 10% pooling: error rate 3.4%, 502s, rolled back in 11 minutes; readiness timeout 1s to 5s
- JSON: 340ms CPU per slow request, 220kb key registry blob vs keys:v3 at 14kb
- key_miss for acme-eu: KeyError 'acme-eu/2026-02', shard=17, registry_v2 timeout 9.0s, validate.duration_ms=9120
- cache-b used_memory=6.1gb maxmemory=6gb evicted_keys=184223; pool acquired=39 max_open=40 wait_ms=1180
- retry:queue:* 3.1 million entries, oldest 2026-02-02; TTL 600s, cap 5,000 per tenant, batches of 10,000 with 50ms sleep
- TTL script ran 26 minutes, evicted_keys 0 by 09:40; p95 380ms
- Paths: /srv/checkout/tokenservice/validate.py lines 214 and 221, /srv/checkout/tokenservice/registry_v3.py line 88
- Failed: 8gb cache-b rolled back to 6gb; disabling retry wrapper lost 0.4% payments for 20 minutes; November single DEL caused 40s downtime

## 5. MISSING OR AMBIGUOUS

- payload_version=2 bump (60kb to 220kb blob) never confirmed as release link
- Flaky ECONNRESET has no ticket number, owner, or deadline
- v3 compressed lookup has no load test and no rollback plan
- Cross-AZ connection setup never measured since pooling; proxy has no date
- Unclear how long 25% canary must hold before full rollout

## 6. FULL SOURCE — nothing was lost

- id: `401b067674` · 144 lines · ~2435 tok · `C:\Users\Pusu\.threadkeep\401b067674.txt`
- any slice, on demand: `threadkeep --show 401b067674 --lines 120-180`