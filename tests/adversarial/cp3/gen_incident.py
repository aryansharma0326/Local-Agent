"""Independent incident-log generator for the CP3 debugging task (TEST ONLY).

Unlike the submission's own fixture (tests/fixtures/gen_logs.py), each service
here uses a different log format, and the chain carries traps:

* gateway  - bracketed text lines; the trace is recorded as ``upstream_trace=``
* orders   - logfmt (``key=value`` with quoted msg); the error code is ``err=``
* inventory- log4j style (``2026-09-17 14:31:41,602`` with a comma), multi-line
             Java stack traces with *nested* ``Caused by:`` lines
* payments - JSON lines (``intent``/``order_ref``/``amount_cents``)

Traps: an earlier 409 attempt for the same order; decoy failed checkouts for
look-alike order ids (with their own stalls and culprit classes); an older,
recovered stall on the *same* worker caused by a different class; the
root-cause class is misspelled in the logs (``StockReconcilationJob``) while a
correctly spelled ``StockReconciliationJob`` appears in unrelated INFO noise;
``GET /api/orders/<order>`` lines after the failure.

  python3 gen_incident.py --seed S --out DIR [--names g,o,i,p] [--order ORD-77341]

Writes DIR/logs/*.log, DIR/answer_key.json and DIR/TASK.md.
"""

import argparse
import json
import os
import random
import string
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 9, 17, 13, 50, 0, tzinfo=timezone.utc)
CULPRITS = ["StockReconcilationJob", "LedgerCompactorV2", "SkuIndexReBuilder", "HoldExpirySweeper"]
CORRECT_SPELLING = {"StockReconcilationJob": "StockReconciliationJob", "LedgerCompactorV2": "LedgerCompactor",
                    "SkuIndexReBuilder": "SkuIndexRebuilder", "HoldExpirySweeper": "HoldExpiryScanner"}
DECOY_CULPRITS = ["PriceCacheRefresher", "CouponBatchLoader", "SearchFeedExporter"]
NOISE = ["CacheWarmup", "MetricsFlusher", "PriceFeedClient", "SessionReaper", "EmailDigestJob"]

PROMPT = """**CONSTRAINT: Your final deliverable is a file named `REPORT.json` in the current directory, containing one JSON
object with exactly the 12 keys listed at the end of this task. Write it before you finish.**

# Incident: checkout failure for order {order}

At 2026-09-17 14:32 UTC a customer's checkout for order `{order}` failed. The browser received:

```
HTTP 502 Bad Gateway  (POST /api/checkout, order {order})
```

Logs from the four services involved are in `logs/`:

- `logs/{g}.log` – API gateway
- `logs/{o}.log` – orders service
- `logs/{i}.log` – inventory service
- `logs/{p}.log` – payments service

Each log is large (roughly 15,000–25,000 tokens) and noisy, and contains many unrelated errors. Services do not
log each other's IDs: each service tags its lines with its own identifiers, and a line in one service tells you
which identifier to look for in the next. Follow this specific failed request from the gateway to the component
that actually caused it.

## REPORT.json

Answer each question below with the value exactly as it appears in the logs. Use JSON strings for IDs, names,
paths and timestamps, and JSON integers for counts and numbers.

1. `gateway_request_id` — the gateway's request ID (`req=`) of the failed POST /api/checkout for {order}.
2. `upstream_trace_id` — the trace ID the gateway recorded for the upstream call of that request.
3. `hold_id` — the inventory hold that the orders service was waiting on for that trace.
4. `worker` — the inventory worker that hold was queued on.
5. `root_cause_component` — the software component that caused the failure (a class name, not a service or worker
   name), named exactly as it is spelled in the logs.
6. `payment_intent_id` — the payment intent created for {order}.
{hidden}
Example shape (values are placeholders):

```json
{{"gateway_request_id": "gw-000000", "upstream_trace_id": "tr-00000000", "hold_id": "hold-000000",
 "worker": "inv-w0", "root_cause_component": "Name", "payment_intent_id": "pi_0000000000",
 ...the six hidden keys...}}
```
"""


HIDDEN_LINES = {
    None: [("gateway_latency_ms", "the latency in milliseconds the gateway logged for that failed request."),
           ("orders_error_code", "the error code the orders service logged when it gave up on that trace."),
           ("hold_queued_at", "the timestamp of the inventory log line that queued that hold, exactly as logged."),
           ("queue_depth", "the worker's queue depth when that hold was queued."),
           ("lock_name", "the name of the lock the root-cause component was holding (without quotes)."),
           ("payment_amount_cents", "the amount, in cents, of the payment intent created for {order}.")],
    "pool": [("gateway_latency_ms", "the latency in milliseconds the gateway logged for that failed request."),
             ("orders_error_code", "the error code the orders service logged when it gave up on that trace."),
             ("hold_queued_at", "the timestamp of the inventory log line that queued that hold, exactly as logged."),
             ("queue_depth", "the worker's queue depth when that hold was queued."),
             ("feed_timeout_ms", "the timeout in milliseconds after which the root-cause component's call gave up."),
             ("payment_amount_cents", "the amount, in cents, of the payment intent created for {order}.")],
    "keys2": [("order_number", "the order number of the failed checkout, exactly as the gateway logged it."),
              ("upstream_service", "the name of the upstream service the gateway called for that request."),
              ("orders_error_code", "the error code the orders service logged when it gave up on that trace."),
              ("hold_queued_at", "the timestamp of the inventory log line that queued that hold, exactly as logged."),
              ("queue_depth", "the worker's queue depth when that hold was queued."),
              ("payment_cancel_event", "the event name of the payments log line that canceled that payment intent.")],
}


def hexid(rng, n):
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


def l4j(t):
    return t.strftime("%Y-%m-%d %H:%M:%S,") + "%03d" % (t.microsecond // 1000)


def brk(t):
    return t.strftime("[%Y-%m-%d %H:%M:%S.") + "%03d]" % (t.microsecond // 1000)


class Log(object):
    def __init__(self):
        self.items = []

    def add(self, t, text):
        self.items.append((t, len(self.items), text))

    def text(self):
        self.items.sort()
        return "\n".join(x[2] for x in self.items) + "\n"


def generate(seed, names, order, variant=None):
    rng = random.Random(seed)
    g, o, i, p = names
    G, O, I, P = Log(), Log(), Log(), Log()
    culprit = CULPRITS[seed % len(CULPRITS)]

    def gw_start(t, req, ordr, path="/api/checkout", method="POST"):
        G.add(t, "%s %s INFO  req=%s %s %s order=%s client=10.%d.%d.%d" % (
            brk(t), g, req, method, path, ordr, rng.randint(0, 255), rng.randint(0, 255), rng.randint(1, 254)))

    def gw_end(t, req, code, reason, tr, lat, ok):
        G.add(t, "%s %s %s req=%s POST /api/checkout -> %d %s upstream=%s upstream_trace=%s latency_ms=%d" % (
            brk(t), g, "INFO " if ok else "ERROR", req, code, reason, o, tr, lat))

    def orders_lines(t, tr, ordr, hold, ok, dur, timeout):
        O.add(t, 'ts=%s level=info svc=%s trace_id=%s msg="checkout started" order=%s' % (iso(t), o, tr, ordr))
        O.add(t + timedelta(milliseconds=9), 'ts=%s level=info svc=%s trace_id=%s msg="awaiting stock reservation" '
              'hold=%s timeout_ms=%d' % (iso(t + timedelta(milliseconds=9)), o, tr, hold, timeout))
        te = t + timedelta(milliseconds=dur)
        if ok:
            O.add(te, 'ts=%s level=info svc=%s trace_id=%s msg="checkout complete"' % (iso(te), o, tr))
        else:
            O.add(te, 'ts=%s level=error svc=%s trace_id=%s msg="stock reservation timed out" hold=%s waited_ms=%d '
                  'err=INV_HOLD_TIMEOUT' % (iso(te), o, tr, hold, dur - 9))

    def inv_queue(t, hold, worker, depth):
        I.add(t, "%s INFO  [%s-exec-%d] c.s.i.HoldService - hold=%s status=CREATED" % (
            l4j(t), worker, rng.randint(1, 4), hold))
        t2 = t + timedelta(milliseconds=rng.randint(3, 30))
        I.add(t2, "%s INFO  [%s-exec-%d] c.s.i.HoldService - hold=%s status=QUEUED worker=%s queue_depth=%d" % (
            l4j(t2), worker, rng.randint(1, 4), hold, worker, depth))
        return t2

    def stall(t, worker, cls, lock, thread, recovered_after=None):
        I.add(t, "%s WARN  [%s] c.s.i.WorkerLoop - worker=%s no progress for %d ms" % (
            l4j(t), thread, worker, rng.randint(5000, 9000)))
        t2 = t + timedelta(seconds=rng.randint(2, 6))
        I.add(t2, "\n".join([
            "%s ERROR [%s] c.s.i.WorkerLoop - worker=%s reservation loop stalled" % (l4j(t2), thread, worker),
            "com.shop.inventory.QueueStalledException: worker %s made no progress for %d ms" % (
                worker, rng.randint(20000, 30000)),
            "\tat com.shop.inventory.WorkerLoop.tick(WorkerLoop.java:212)",
            "\tat com.shop.inventory.WorkerLoop.run(WorkerLoop.java:88)",
            "Caused by: com.shop.inventory.ReservationRejected: reservation rejected after lock wait",
            "\tat com.shop.inventory.HoldService.apply(HoldService.java:97)",
            "\t... 6 more",
            "Caused by: com.shop.storage.LockWaitTimeout: lock '%s' held by %s (thread %s)" % (
                lock, cls, "recon-%d" % rng.randint(1, 9)),
            "\tat com.shop.storage.LedgerLocks.acquire(LedgerLocks.java:51)",
            "\tat com.shop.jobs.%s.run(%s.java:%d)" % (cls, cls, rng.randint(40, 400)),
            "\t... 14 more"]))
        if recovered_after:
            t3 = t2 + recovered_after
            I.add(t3, "%s INFO  [%s] c.s.i.WorkerLoop - worker=%s recovered; lock '%s' released" % (
                l4j(t3), thread, worker, lock))

    # ---------------------------------------------------------------- noise
    t = BASE
    k = 0
    while t < BASE + timedelta(minutes=58):
        t += timedelta(milliseconds=rng.randint(9000, 17000))
        k += 1
        ordr = "ORD-%05d" % rng.randint(10000, 99999)
        if ordr.startswith(order[:-1]):
            continue
        req, tr = "gw-" + hexid(rng, 6), "tr-" + hexid(rng, 8)
        hold, wk = "hold-%06d" % rng.randint(100000, 999999), "inv-w%d" % rng.randint(1, 8)
        ok = rng.random() > 0.05
        timeout = rng.choice([15000, 20000, 30000])
        dur = rng.randint(90, 900) if ok else timeout + rng.randint(4, 40)
        gw_start(t, req, ordr)
        orders_lines(t + timedelta(milliseconds=6), tr, ordr, hold, ok, dur, timeout)
        inv_queue(t + timedelta(milliseconds=20), hold, wk, rng.randint(0, 7))
        if ok:
            tc = t + timedelta(milliseconds=dur - 30)
            I.add(tc, "%s INFO  [%s-exec-%d] c.s.i.HoldService - hold=%s status=CONFIRMED" % (
                l4j(tc), wk, rng.randint(1, 4), hold))
        gw_end(t + timedelta(milliseconds=dur + 12), req, 200 if ok else 502, "OK" if ok else "Bad Gateway", tr,
               dur + 12, ok)
        tp = t + timedelta(milliseconds=15)
        P.add(tp, json.dumps({"time": iso(tp), "lvl": "INFO", "service": p, "event": "payment_intent.created",
                              "intent": "pi_" + "".join(rng.choice(string.ascii_letters + string.digits)
                                                        for _ in range(16)),
                              "order_ref": ordr, "amount_cents": rng.randint(500, 40000)}))
        if k % 4 == 0:
            gw_start(t + timedelta(milliseconds=300), "gw-" + hexid(rng, 6), "-", "/healthz", "GET")
        if k % 6 == 0:
            c = rng.choice(NOISE)
            tn = t + timedelta(milliseconds=700)
            O.add(tn, 'ts=%s level=error svc=%s job=%s msg="background job failed" err=JOB_FAILED' % (iso(tn), o, c))
            I.add(tn, "\n".join(["%s ERROR [bg-%d] c.s.i.Jobs - %s failed" % (l4j(tn), rng.randint(1, 9), c),
                                 "java.lang.RuntimeException: %s failed" % c,
                                 "\tat com.shop.jobs.%s.run(%s.java:%d)" % (c, c, rng.randint(10, 300))]))
        if k % 9 == 0:
            tn = t + timedelta(milliseconds=800)
            I.add(tn, "%s INFO  [sched-1] c.s.j.Scheduler - %s finished run %d in %d ms" % (
                l4j(tn), CORRECT_SPELLING[culprit], rng.randint(100, 999), rng.randint(200, 4000)))
        if k % 10 == 0:
            tn = t + timedelta(milliseconds=900)
            P.add(tn, json.dumps({"time": iso(tn), "lvl": "ERROR", "service": p, "event": "webhook.failed",
                                  "endpoint": "https://hooks.example.com/%d" % rng.randint(1, 50),
                                  "status": rng.choice([500, 503, 504])}))

    # ---------------------------------------------------------------- incident
    worker_n = rng.randint(1, 8)
    worker = "inv-w%d" % worker_n
    thread = "%s-exec-%d" % (worker, rng.randint(1, 4))
    timeout = rng.choice([20000, 25000, 30000])
    t_start = datetime(2026, 9, 17, 14, 31, rng.randint(5, 25), rng.randint(0, 999) * 1000, tzinfo=timezone.utc)
    req, tr = "gw-" + hexid(rng, 6), "tr-" + hexid(rng, 8)
    hold = "hold-%06d" % rng.randint(100000, 999999)
    depth = rng.randint(17, 48)
    lock = "sku-ledger#%d" % rng.randint(2, 19)
    pi = "pi_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(16))
    cents = rng.randint(2000, 90000)

    # earlier attempt for the same order: 409, no hold, no payment
    t_prev = t_start - timedelta(seconds=rng.randint(60, 90))
    req_prev, tr_prev = "gw-" + hexid(rng, 6), "tr-" + hexid(rng, 8)
    gw_start(t_prev, req_prev, order)
    O.add(t_prev + timedelta(milliseconds=7), 'ts=%s level=warn svc=%s trace_id=%s msg="cart version mismatch" '
          'order=%s err=CART_STALE' % (iso(t_prev + timedelta(milliseconds=7)), o, tr_prev, order))
    gw_end(t_prev + timedelta(milliseconds=41), req_prev, 409, "Conflict", tr_prev, 41, True)

    # older, recovered stall on the same worker caused by a different class
    stall(t_start - timedelta(minutes=rng.randint(25, 35)), worker, rng.choice(DECOY_CULPRITS),
          "sku-ledger#%d" % rng.randint(20, 40), thread, recovered_after=timedelta(seconds=rng.randint(20, 50)))

    # the real chain
    gw_start(t_start, req, order)
    if variant == "retry_up":
        tr_first = "tr-" + hexid(rng, 8)
        ta = t_start + timedelta(milliseconds=3)
        O.add(ta, 'ts=%s level=info svc=%s trace_id=%s msg="checkout started" order=%s' % (iso(ta), o, tr_first, order))
        tb = ta + timedelta(milliseconds=4)
        O.add(tb, 'ts=%s level=warn svc=%s trace_id=%s msg="client connection reset before reservation"' %
              (iso(tb), o, tr_first))
        tc_ = t_start + timedelta(milliseconds=6)
        G.add(tc_, "%s %s WARN  req=%s upstream attempt 1 failed: connection reset upstream=%s upstream_trace=%s; "
                   "retrying" % (brk(tc_), g, req, o, tr_first))
    dur = timeout + rng.randint(9, 60)
    orders_lines(t_start + timedelta(milliseconds=8), tr, order, hold, False, dur, timeout)
    tp = t_start + timedelta(milliseconds=14)
    P.add(tp, json.dumps({"time": iso(tp), "lvl": "INFO", "service": p, "event": "payment_intent.created",
                          "intent": pi, "order_ref": order, "amount_cents": cents}))
    t_q = inv_queue(t_start + timedelta(milliseconds=25), hold, worker, depth)
    feed_ms = 25000
    if variant == "pool":
        culprit = "SupplierFeedClient"
        ts_ = t_q - timedelta(seconds=rng.randint(4, 12))
        I.add(ts_, "%s WARN  [%s] c.s.i.SupplierFeedClient - stock feed call blocking worker=%s" % (l4j(ts_), thread, worker))
        te_ = ts_ + timedelta(seconds=3)
        I.add(te_, "\n".join([
            "%s ERROR [%s] c.s.i.WorkerLoop - worker=%s reservation loop stalled" % (l4j(te_), thread, worker),
            "com.shop.inventory.QueueStalledException: worker %s made no progress for %d ms" % (worker, feed_ms + 900),
            "\tat com.shop.inventory.WorkerLoop.tick(WorkerLoop.java:212)",
            "Caused by: com.shop.inventory.ReservationRejected: reservation rejected after supplier wait",
            "\tat com.shop.inventory.HoldService.apply(HoldService.java:97)",
            "\t... 6 more",
            "Caused by: java.util.concurrent.TimeoutException: supplier stock feed did not answer in %d ms" % feed_ms,
            "\tat java.util.concurrent.FutureTask.get(FutureTask.java:205)",
            "\tat com.shop.inventory.SupplierFeedClient.fetch(SupplierFeedClient.java:88)",
            "\tat com.shop.inventory.HoldService.reserve(HoldService.java:61)",
            "\t... 11 more"]))
    else:
        stall(t_q - timedelta(seconds=rng.randint(4, 12)), worker, culprit, lock, thread)
    t_end = t_start + timedelta(milliseconds=dur + 20)
    lat = dur + 20
    gw_end(t_end, req, 502, "Bad Gateway", tr, lat, False)
    tc = t_end + timedelta(milliseconds=350)
    P.add(tc, json.dumps({"time": iso(tc), "lvl": "INFO", "service": p, "event": "payment_intent.canceled",
                          "intent": pi, "order_ref": order, "reason": "checkout_failed"}))

    # decoy failures for look-alike orders, with their own culprits
    for d in (order + "0", order[:-2] + order[-1] + order[-2]):
        td = t_start + timedelta(seconds=rng.randint(-40, 40))
        dw = "inv-w%d" % (worker_n % 8 + 1)
        dreq, dtr = "gw-" + hexid(rng, 6), "tr-" + hexid(rng, 8)
        dhold = "hold-%06d" % rng.randint(100000, 999999)
        gw_start(td, dreq, d)
        ddur = timeout + rng.randint(9, 60)
        orders_lines(td + timedelta(milliseconds=8), dtr, d, dhold, False, ddur, timeout)
        tq = inv_queue(td + timedelta(milliseconds=25), dhold, dw, rng.randint(10, 40))
        stall(tq - timedelta(seconds=rng.randint(3, 9)), dw, rng.choice(DECOY_CULPRITS),
              "sku-ledger#%d" % rng.randint(20, 40), dw + "-exec-1")
        gw_end(td + timedelta(milliseconds=ddur + 20), dreq, 502, "Bad Gateway", dtr, ddur + 20, False)
        P.add(td + timedelta(milliseconds=14), json.dumps({
            "time": iso(td + timedelta(milliseconds=14)), "lvl": "INFO", "service": p,
            "event": "payment_intent.created", "intent": "pi_" + "".join(
                rng.choice(string.ascii_letters + string.digits) for _ in range(16)),
            "order_ref": d, "amount_cents": rng.randint(2000, 90000)}))
    for s in (5, 47):
        tg = t_end + timedelta(seconds=s)
        gw_start(tg, "gw-" + hexid(rng, 6), order, "/api/orders/%s" % order, "GET")

    values = {"gateway_request_id": req, "upstream_trace_id": tr, "hold_id": hold, "worker": worker,
              "root_cause_component": culprit, "payment_intent_id": pi, "gateway_latency_ms": lat,
              "orders_error_code": "INV_HOLD_TIMEOUT", "hold_queued_at": l4j(t_q), "queue_depth": depth,
              "lock_name": lock, "payment_amount_cents": cents, "feed_timeout_ms": feed_ms, "order_number": order,
              "upstream_service": o, "payment_cancel_event": "payment_intent.canceled"}
    hidden = HIDDEN_LINES.get(variant, HIDDEN_LINES[None])
    key = dict((k, values[k]) for k in ["gateway_request_id", "upstream_trace_id", "hold_id", "worker",
                                         "root_cause_component", "payment_intent_id"])
    for k, _ in hidden:
        key[k] = values[k]
    hidden_txt = "".join("%d. `%s` — %s\n" % (7 + j, k, d.format(order=order)) for j, (k, d) in enumerate(hidden))
    prompt = PROMPT.format(order=order, g=g, o=o, i=i, p=p, hidden=hidden_txt)
    return {g: G.text(), o: O.text(), i: I.text(), p: P.text()}, key, prompt


def write(seed, out, names=("gateway", "orders", "inventory", "payments"), order="ORD-77341", variant=None):
    logs, key, prompt = generate(seed, list(names), order, variant)
    os.makedirs(os.path.join(out, "logs"), exist_ok=True)
    for name, text in logs.items():
        with open(os.path.join(out, "logs", name + ".log"), "w") as f:
            f.write(text)
    with open(os.path.join(out, "TASK.md"), "w") as f:
        f.write(prompt)
    return key, prompt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--order", default="ORD-77341")
    ap.add_argument("--names", default="gateway,orders,inventory,payments")
    ap.add_argument("--variant", default=None, choices=[None, "pool", "keys2", "retry_up"])
    a = ap.parse_args()
    key, _ = write(a.seed, a.out, a.names.split(","), a.order, a.variant)
    with open(os.path.join(a.out, "answer_key.json"), "w") as f:
        json.dump(key, f, indent=1)


if __name__ == "__main__":
    main()
