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

1. Latency regression traced to 2026-02-11 release; first hypothesis: retry wrapper retrying non-idempotent POSTs.
   - why: p95 went 180ms to 2.4s, support tickets doubled
   - evidence: “We started this because checkout latency went from 180ms p95 to 2.4s p95 after the 2026-02-11 release, and sup” [L3-6]
2. Ahmed showed slow spans sit inside tokenservice.validate; retry theory parked, proper measurement started.
   - why: max span in validate was 9.1s, not retry path
   - evidence: “Ahmed disagreed. His reading of the traces: the slow spans are all inside `tokenservice.validate`, not in the ” [L8-10]
3. Built k6 synthetic load test against staging; it failed to reproduce the slowdown.
   - why: staging stayed at 240ms p95, problem is production-only
   - evidence: “19 Feb — built a synthetic load test (k6, 300 rps, 10 min) against staging. Result: staging never goes above 2” [L12-15]
4. Production span sampling found cache-b hotspot; raised maxmemory 4gb to 6gb; p95 fell to 640ms.
   - why: eviction storms explained 9s validate spans
   - evidence: “20 Feb — turned on span sampling in production for 1% of traffic, 6 hours. Found that 71% of slow requests hit” [L17-25]
5. Re-enabled connection pooling with max_idle=8, keeping max_open=40; staging run showed one unreproducible ECONNRESET marked flaky.
   - why: new TLS connection per request; old incident came from 400 open connections
   - evidence: “22 Feb — Sofia noticed that the remaining slow requests all come from a single AZ (eu-central-1c). Cross-AZ la” [L27-36]
6. Pooling at 10% prod caused 502 storm and rollback; raised readiness probe timeout to 5s, retried, then asked platform for dedicated connection proxy.
   - why: pooled idle connections made first request slower than 1s probe timeout
   - evidence: “24 Feb — rolled pooling to 10% of production. p95 610ms. Then at 14:20 the error rate jumped to 3.4% with 502s” [L38-48]
7. Decided to replace full 220kb registry parse with keys:v3 14kb compressed lookup; flagged risk of no load test, no rollback, canary required.
   - why: JSON serialization cost 340ms CPU per slow request
   - evidence: “26 Feb — target was p95 < 400ms. We are at 600ms. Remaining hot spot per profiler: JSON serialization in valid” [L50-57]
8. v3 canary held at 1% while key_miss on acme-eu unexplained; fallback kept enabled, blocked on Marko.
   - why: unclear if tenants lack v3 entries or exporter dropped them
   - evidence: “27 Feb — v3 lookup behind a flag on 1% of prod. Canary numbers: p95 430ms, errors 0.02% (same as baseline), bu” [L65-71]
9. Found retry:queue:* holding 3.1M entries; decided on 600s TTL and 5,000-entry cap applied in batches.
   - why: untrimmed retry bookkeeping pushed cache-b into eviction
   - evidence: “28 Feb — checked key counts. `retry:queue:*` holds 3.1 million entries, oldest from 2026-02-02. So Ahmed was r” [L98-107]
10. TTL script ran clean; evicted_keys hit 0, p95 reached 380ms target; pooling kept at 10%.
   - why: readiness timeout change must survive a week
   - evidence: “1 March — TTL script ran in 26 minutes, evicted_keys dropped to 0 by 09:40, p95 in prod 380ms. Target hit for ” [L109-113]
11. Marko confirmed key_miss is expected exporter behavior, not data loss; v3 rollout extended to 25% plus backfill of 4 tenants; own 3 March deadline slipped.
   - why: tenants silently ride slow path until backfill lands
   - evidence: “2 March — Marko is back. He confirmed the exporter skips tenants whose registry was written before the v3 migr” [L115-125]
12. Proxy approved for next sprint with no date; recorded three failed approaches: 8gb cache, disabling retry wrapper, read replica.
   - why: 8gb no effect, wrapper removal broke checkout, replica lag gave stale keys
   - evidence: “5 March — The dedicated connection proxy got approved by platform; it lands next sprint, no date yet. In the m” [L127-134]

## 4. FACTS, NUMBERS, NAMES

- p95 180ms before release, 2.4s on release day, 412 tickets up from 205
- tokenservice.validate max span 9.1s
- k6, 300 rps, 10 min; staging p95 240ms
- 71% of slow requests hit cache-b, 96% memory used
- maxmemory 4gb to 6gb; p95 then 640ms
- eu-central-1c; max_idle=8 per host; max_open=40 per pod
- 1 failed of 180,000 requests, ECONNRESET, not reproducible in 3 runs
- 3.4% error rate, 502s from gateway, rolled back in 11 minutes; readiness timeout 1s to 5s
- json.loads on 220kb blob, 340ms CPU; keys:v3 is 14kb
- canary: p95 430ms, errors 0.02%, tenant acme-eu key_miss; Marko on leave until 2 March
- evicted_keys=184223, used_memory=6.1gb, pool acquired=39 wait_ms=1180, validate.duration_ms=9120
- retry:queue:* 3.1 million entries, oldest 2026-02-02; TTL 600s, cap 5,000, batches of 10,000 with 50ms sleep
- TTL script 26 minutes, evicted_keys 0 by 09:40, p95 380ms; cron every 6h
- /srv/checkout/tokenservice/registry_v3.py line 88; backfill owner Marko, deadline 6 March
- 8gb attempt rolled back to 6gb; wrapper removal lost 0.4% payments for 20 minutes; November DEL caused 40 seconds downtime

## 5. MISSING OR AMBIGUOUS

- Whether payload_version bump to 2 caused registry blob growth remains unconfirmed.
- No load test or rollback plan for keys:v3 compressed lookup path.
- Cross-AZ connection setup never measured since pooling was re-enabled.
- Flaky ECONNRESET ticket has no owner, priority, or deadline.
- Proxy date unknown; whether it fixes the AZ problem unmeasured.

## 6. FULL SOURCE — nothing was lost

- id: `401b067674` · 144 lines · ~2435 tok · `C:\Users\Pusu\.threadkeep\401b067674.txt`
- any slice, on demand: `threadkeep --show 401b067674 --lines 120-180`