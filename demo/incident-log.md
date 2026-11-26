# Checkout service — running notes (messy, real, unedited)

We started this because checkout latency went from 180ms p95 to 2.4s p95 after the
2026-02-11 release, and support tickets about "payment spinner forever" doubled
in one week (412 tickets, up from 205). First guess from me: the new retry
wrapper added in the release was retrying non-idempotent POSTs.

Ahmed disagreed. His reading of the traces: the slow spans are all inside
`tokenservice.validate`, not in the retry path. Max span there is 9.1s. So we
parked the retry theory and started measuring properly.

19 Feb — built a synthetic load test (k6, 300 rps, 10 min) against staging.
Result: staging never goes above 240ms p95. So the problem does not reproduce
outside production. Waste of half a day, but useful: it kills the "it's just
slow everywhere" theory.

20 Feb — turned on span sampling in production for 1% of traffic, 6 hours.
Found that 71% of slow requests hit the same Redis cluster (cache-b), and that
cluster has 96% memory used at the moment of the spike. Decision: raise
maxmemory from 4gb to 6gb on cache-b and watch for a day. Rationale: eviction
storms would explain the 9s validate spans, since validate reads the key
registry from that cluster.

21 Feb — after the resize: p95 dropped to 640ms. Not fixed, but 3.7x better.
So eviction was part of it, not all of it.

22 Feb — Sofia noticed that the remaining slow requests all come from a single
AZ (eu-central-1c). Cross-AZ latency to cache-b is fine, but connection setup
is not: we create a new TLS connection per request because the connection pool
was disabled during the "fix pool exhaustion" incident in January. Decision:
re-enable pooling with max_idle=8 per host. Must keep max_open=40, because the
old incident came from 400 open connections per pod. Do not repeat that.

23 Feb — pooling re-enabled in staging. Load test: 1 failed request out of
180,000, error was ECONNRESET, and we could not reproduce it again in 3 runs.
Marked as flaky, not fixed, needs a follow-up ticket.

24 Feb — rolled pooling to 10% of production. p95 610ms. Then at 14:20 the
error rate jumped to 3.4% with 502s from the gateway. Rolled back in 11 minutes.
Root cause of the 502s: our readiness probe timeout is 1s and pooled connections
made the first request after idle slower than 1s, so pods got pulled out of the
load balancer while still healthy. Decision: raise readiness timeout to 5s before
trying pooling again.

25 Feb — readiness timeout raised, pooling at 10% again, no 502s for 6 hours.
p95 600ms. Sent the numbers to the platform team and asked for a dedicated
connection proxy instead of per-pod pools, because we are now at 6gb memory on
cache-b and still not at the target.

26 Feb — target was p95 < 400ms. We are at 600ms. Remaining hot spot per
profiler: JSON serialization in validate, 340ms of CPU per slow request,
`json.loads` on a 220kb key registry blob. Decision: stop parsing the whole
registry on every request; use the `keys:v3` compressed lookup instead, which
is 14kb. Owner: me. Deadline: 3 March.

Not yet tested: the compressed lookup path has no load test yet, and we have
no rollback plan if v3 misses keys. Do not ship it to 100% without a canary.

Open question nobody answered: why did p95 jump exactly at the release, if the
real cause is Redis eviction plus pool exhaustion? The release only changed the
retry wrapper. Possibly it also bumped `payload_version` to 2, which made the
key registry blob grow from 60kb to 220kb. That would tie everything together,
but nobody has confirmed it yet.

27 Feb — v3 lookup behind a flag on 1% of prod. Canary numbers: p95 430ms,
errors 0.02% (same as baseline), but two pods logged `key_miss` for tenant
`acme-eu` and fell back to the slow path. Nobody knows yet whether those tenants
simply have no v3 entries or whether the exporter dropped them. Blocked on
Marko from the data team, who is on leave until 2 March. Decision: hold the
canary at 1%, do not expand while key_miss is unexplained. Do not disable the
fallback — the fallback is what keeps checkout working.

Stack trace from one of the key_miss pods (pasted here in full because I keep
losing the file):

    File "/srv/checkout/tokenservice/validate.py", line 214, in _lookup
        entry = self._registry.get(tenant_key)
    File "/srv/checkout/tokenservice/registry_v3.py", line 88, in get
        return self._decode(self._blobs[shard])
    KeyError: 'acme-eu/2026-02'
    During handling of the above exception, another exception occurred:
    File "/srv/checkout/tokenservice/validate.py", line 221, in _lookup
        return self._lookup_v2(tenant_key)  # fallback, slow path
    TimeoutError: registry_v2 read timed out after 9.0s
    INFO: request_id=7c1a... tenant=acme-eu shard=17 route=checkout/confirm
    INFO: cache-b used_memory=6.1gb maxmemory=6gb evicted_keys=184223
    INFO: pool: acquired=39 max_open=40 wait_ms=1180
    INFO: validate.duration_ms=9120

That last line matters: `evicted_keys=184223` at 6gb means cache-b is *still*
under eviction pressure even after the resize, so the 6gb ceiling was not enough
and the memory growth is not just the registry blob. Ahmed thinks the retry
wrapper logs are inflating a Redis list that nobody trims. He is probably right.
We should check `retry:queue:*` keys — untested so far, and nobody has looked at
key counts per prefix. Note for later: if that list is unbounded, we are also
paying for it in memory, not just latency.

28 Feb — checked key counts. `retry:queue:*` holds 3.1 million entries, oldest
from 2026-02-02. So Ahmed was right: the retry wrapper added in the release
pushes an entry per attempt and nothing trims it, and that is what pushed cache-b
into eviction. So the release *did* cause it, indirectly, and the retry theory
was half right after all — not because retries were non-idempotent, but because
their bookkeeping floods Redis. Decision: add a TTL of 600s on `retry:queue:*`
and cap the list at 5,000 entries per tenant. Must be applied with a script that
does not block on 3.1M keys — do it in batches of 10,000 with a 50ms sleep,
never a single `DEL` on the whole prefix (that stalls the cluster, we did it once
in November and took 40 seconds of downtime).

1 March — TTL script ran in 26 minutes, evicted_keys dropped to 0 by 09:40,
p95 in prod 380ms. Target hit for the first time since 11 Feb. Kept pooling at
10% — do not go to 100% until the readiness timeout change has survived a week.
The v3 canary is still at 1% and the key_miss question is still open, but it now
matters much less because the slow path is fast again.

2 March — Marko is back. He confirmed the exporter skips tenants whose registry
was written before the v3 migration, so the key_miss is expected for `acme-eu`
and 3 other tenants, not data loss. Decision: finish the v3 rollout to 25% and
write a backfill job for those 4 tenants. Owner: Marko. Deadline: 6 March. Do
not call the v3 path "done" until the backfill is in, because until then those
tenants silently ride the slow path and we would not notice if the slow path
regressed again.

3 March — deadline for my own item (Keys v3 default) was today and it slipped
because of the key_miss investigation. Decided to keep the canary at 25% and not
promise a new date until the backfill lands.

5 March — The dedicated connection proxy got approved by platform; it lands next
sprint, no date yet. In the meantime: pooling stays at 10%, readiness timeout
stays at 5s, TTL script runs in a cron every 6h to keep `retry:queue:*` trimmed.
Things we tried that did not work, so nobody repeats them: (a) raising cache-b
memory to 8gb — no effect while the retry list kept growing, we rolled it back
to 6gb; (b) disabling the retry wrapper entirely — fixed Redis but made checkout
fail on transient 503s, we lost 0.4% of payments for 20 minutes before rollback;
(c) moving validate to a read replica — replica lag made stale keys, rejected.

Still open: whether the connection proxy actually removes the AZ problem (nobody
has measured cross-AZ connection setup since pooling), the 4-tenant backfill, the
flaky ECONNRESET ticket, and whether p95 stays under 400ms for a full week.

Reminder to self about how we got here, in case I write this up later: baseline
180ms p95 (before 11 Feb) -> 2.4s (release day) -> 640ms (cache-b memory) ->
600ms (readiness timeout) -> 380ms (retry TTL). Four separate causes stacked,
and each fix looked "not enough" on its own, which is why we kept doubting the
numbers instead of the theory.
