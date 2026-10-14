"""Seeded 4-service log generator for debugging tasks (TEST ONLY).

  python3 gen_logs.py --seed S --out DIR [--scenarios retry,decoy_ids,decoy_class,json_lines,mixed_ts]
                      [--order ORD-77341] [--hidden 6|0] [--names gateway,orders,inventory,payments]

Writes DIR/logs/<service>.log (each ~15-25k tokens of noise plus one failed
request chain), DIR/answer_key.json and DIR/TASK.md (the official prompt with
6 invented hidden keys, or the 6 official keys only with --hidden 0).

Chain: gateway (req=gw-..., POST /api/checkout, 502, upstream trace=tr-...)
 -> orders (trace=tr-...; "waiting on inventory hold hold-...", timeout)
 -> inventory (hold=hold-... queued on worker=inv-wN, queue_depth)
 -> inventory worker inv-wN stack trace whose innermost Caused-by names the
    root-cause class (lock held by it)
 + payments (payment intent pi_... created for the order, later canceled).
"""

import argparse
import json
import os
import random
import string
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)
CLASSES = ["ReservationLedgerCompactor", "StockSnapshotWriter", "HoldExpiryScanner", "SkuIndexRebuilder"]
NOISE_CLASSES = ["CacheWarmup", "MetricsFlusher", "PriceFeedClient", "CouponValidator", "SessionReaper"]

PROMPT = """**CONSTRAINT: Your final deliverable is a file named `REPORT.json` in the current directory, containing one JSON
object with exactly the {n} keys listed at the end of this task. Write it before you finish.**

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

HIDDEN = [
    ("gateway_status", "the HTTP status code the gateway returned for that request."),
    ("orders_timeout_ms", "the timeout in milliseconds after which the orders service gave up waiting on the hold."),
    ("hold_queued_at", "the timestamp at which the inventory service queued that hold, exactly as logged."),
    ("worker_queue_depth", "the queue depth of the worker when the hold was queued."),
    ("lock_name", "the name of the lock that the root-cause component was holding."),
    ("payment_status", "the final status of the payment intent created for the order."),
]


def hexid(rng, n):
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def ts(t, style="iso"):
    if style == "offset":
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03d+00:00" % (t.microsecond // 1000)
    if style == "epoch":
        return str(int(t.timestamp() * 1000))
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


class Log:
    def __init__(self, style="iso", jsonl=False):
        self.items = []
        self.style = style
        self.jsonl = jsonl

    def add(self, t, level, svc, fields, msg, extra=()):
        if self.jsonl:
            obj = {"ts": ts(t, self.style), "level": level, "service": svc}
            obj.update(fields)
            obj["msg"] = msg
            if extra:
                obj["stack"] = "\n".join(extra)
            self.items.append((t, json.dumps(obj)))
        else:
            kv = " ".join("%s=%s" % kv for kv in fields.items())
            line = "%s %-5s %s %s %s" % (ts(t, self.style), level, svc, kv, msg)
            self.items.append((t, "\n".join([line] + list(extra))))

    def text(self):
        self.items.sort(key=lambda x: x[0])
        return "\n".join(s for _, s in self.items) + "\n"


def generate(seed, scenarios, order, names, hidden_n):
    rng = random.Random(seed)
    g, o, i, p = names
    jl = "json_lines" in scenarios
    mixed = "mixed_ts" in scenarios
    G, O = Log(), Log("offset" if mixed else "iso")
    I, P = Log(jsonl=jl), Log("epoch" if mixed else "iso")
    rc = CLASSES[seed % len(CLASSES)]
    w_n = rng.randint(1, 8)
    worker = "inv-w%d" % w_n
    t_fail = BASE + timedelta(minutes=32, seconds=rng.randint(5, 50), milliseconds=rng.randint(0, 999))
    req, trace = "gw-" + hexid(rng, 6), "tr-" + hexid(rng, 8)
    hold = "hold-%06d" % rng.randint(100000, 999999)
    pi = "pi_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(14))
    timeout_ms = rng.choice([15000, 20000, 25000, 30000])
    depth = rng.randint(9, 40)
    lock = "ledger-shard-%d" % rng.randint(1, 12)
    thread = "%s-exec-%d" % (worker, rng.randint(1, 4))
    t_start = t_fail - timedelta(milliseconds=timeout_ms + rng.randint(100, 900))
    t_queue = t_start + timedelta(milliseconds=rng.randint(40, 300))

    def chain(ordr, t0, ok, rq, tr, hd, wk, status=None):
        dur = rng.randint(80, 900) if ok else timeout_ms + rng.randint(5, 60)
        G.add(t0, "INFO", g, {"req": rq, "method": "POST", "path": "/api/checkout", "order": ordr},
              "request received")
        O.add(t0 + timedelta(milliseconds=12), "INFO", o, {"trace": tr}, "checkout started order=%s" % ordr)
        O.add(t0 + timedelta(milliseconds=30), "INFO", o, {"trace": tr}, "reserving stock; waiting on inventory hold %s" % hd)
        I.add(t0 + timedelta(milliseconds=45), "INFO", i, {"hold": hd}, "hold created")
        I.add(t0 + timedelta(milliseconds=60), "INFO", i, {"hold": hd, "worker": wk, "queue_depth": rng.randint(0, 8)},
              "hold queued")
        if ok:
            I.add(t0 + timedelta(milliseconds=dur - 50), "INFO", i, {"hold": hd}, "hold confirmed")
            O.add(t0 + timedelta(milliseconds=dur - 20), "INFO", o, {"trace": tr}, "checkout complete")
        else:
            O.add(t0 + timedelta(milliseconds=dur - 5), "ERROR", o, {"trace": tr},
                  "gave up waiting on inventory hold %s after %dms" % (hd, timeout_ms))
        G.add(t0 + timedelta(milliseconds=dur), "INFO" if ok else "ERROR", g,
              {"req": rq, "status": status or (200 if ok else 502), "upstream": o, "trace": tr, "duration_ms": dur},
              "POST /api/checkout completed")

    # --- noise: other orders, health checks, unrelated errors, GC
    t = BASE
    k = 0
    while t < BASE + timedelta(minutes=60):
        t += timedelta(milliseconds=rng.randint(6500, 14000))
        k += 1
        ordr = "ORD-%05d" % rng.randint(10000, 99999)
        if ordr.startswith("ORD-7734"):
            continue
        chain(ordr, t, rng.random() > 0.06, "gw-" + hexid(rng, 6), "tr-" + hexid(rng, 8),
              "hold-%06d" % rng.randint(100000, 999999), "inv-w%d" % rng.randint(1, 8))
        P.add(t + timedelta(milliseconds=200), "INFO", p, {"payment_intent": "pi_" + hexid(rng, 14)},
              "payment intent created for order %s" % ordr)
        if k % 3 == 0:
            G.add(t + timedelta(milliseconds=500), "INFO", g, {"req": "gw-" + hexid(rng, 6), "method": "GET",
                                                                "path": "/healthz", "status": 200}, "ok")
        if k % 7 == 0:
            c = rng.choice(NOISE_CLASSES)
            for L, svc in ((O, o), (I, i), (P, p)):
                if rng.random() < 0.4:
                    L.add(t + timedelta(milliseconds=700), "ERROR", svc, {"thread": "bg-%d" % rng.randint(1, 9)},
                          "background task failed", ["java.lang.RuntimeException: %s failed" % c,
                                                     "    at com.shop.%s.run(%s.java:%d)" % (c, c, rng.randint(10, 300))])
        if k % 11 == 0:
            I.add(t + timedelta(milliseconds=900), "WARN", i, {"gc": "G1"}, "GC pause %dms" % rng.randint(20, 400))

    # --- the incident chain
    if "retry" in scenarios:
        r2, t2 = "gw-" + hexid(rng, 6), "tr-" + hexid(rng, 8)
        chain(order, t_fail - timedelta(seconds=65), True, r2, t2, "hold-%06d" % rng.randint(100000, 999999),
              "inv-w%d" % rng.randint(1, 8), status=409)
    G.add(t_start, "INFO", g, {"req": req, "method": "POST", "path": "/api/checkout", "order": order}, "request received")
    O.add(t_start + timedelta(milliseconds=15), "INFO", o, {"trace": trace}, "checkout started order=%s" % order)
    O.add(t_start + timedelta(milliseconds=33), "INFO", o, {"trace": trace},
          "reserving stock; waiting on inventory hold %s" % hold)
    P.add(t_start + timedelta(milliseconds=25), "INFO", p, {"payment_intent": pi}, "payment intent created for order %s" % order)
    I.add(t_queue - timedelta(milliseconds=10), "INFO", i, {"hold": hold}, "hold created")
    I.add(t_queue, "INFO", i, {"hold": hold, "worker": worker, "queue_depth": depth}, "hold queued")
    q_ts = ts(t_queue, I.style)
    t_block = t_start - timedelta(seconds=rng.randint(5, 40))
    I.add(t_block, "WARN", i, {"worker": worker, "thread": thread}, "worker stalled; queue not draining")
    I.add(t_block + timedelta(seconds=2), "ERROR", i, {"worker": worker, "thread": thread},
          "reservation processing blocked",
          ["java.lang.IllegalStateException: reservation queue blocked on %s" % worker,
           "    at com.shop.inventory.HoldQueue.await(HoldQueue.java:88)",
           "    at com.shop.inventory.WorkerLoop.run(WorkerLoop.java:41)",
           "Caused by: com.shop.inventory.LedgerLockTimeout: lock %s held by %s" % (lock, rc),
           "    at com.shop.inventory.%s.compact(%s.java:203)" % (rc, rc),
           "    ... 7 more"])
    # symptom: loud orders-service exception for the same trace
    O.add(t_fail - timedelta(milliseconds=8), "ERROR", o, {"trace": trace},
          "gave up waiting on inventory hold %s after %dms" % (hold, timeout_ms),
          ["com.shop.orders.UpstreamTimeoutException: inventory hold %s not confirmed" % hold,
           "    at com.shop.orders.CheckoutService.reserve(CheckoutService.java:131)"])
    dur = int((t_fail - t_start).total_seconds() * 1000)
    G.add(t_fail, "ERROR", g, {"req": req, "status": 502, "upstream": o, "trace": trace, "duration_ms": dur},
          "POST /api/checkout completed")
    P.add(t_fail + timedelta(milliseconds=400), "INFO", p, {"payment_intent": pi, "status": "canceled"},
          "payment intent canceled: order checkout failed")

    if "decoy_ids" in scenarios:
        for d in (order + "0", order[:-2] + order[-1] + order[-2]):
            chain(d, t_fail - timedelta(seconds=rng.randint(3, 20)), False, "gw-" + hexid(rng, 6),
                  "tr-" + hexid(rng, 8), "hold-%06d" % rng.randint(100000, 999999), "inv-w%d" % rng.randint(1, 8))
            P.add(t_fail, "INFO", p, {"payment_intent": "pi_" + hexid(rng, 14)}, "payment intent created for order %s" % d)
        G.add(t_fail + timedelta(seconds=3), "INFO", g, {"req": "gw-" + hexid(rng, 6), "method": "GET",
                                                          "path": "/api/orders/%s" % order, "status": 200}, "ok")
    if "decoy_class" in scenarios:
        other = "inv-w%d" % (w_n % 8 + 1)
        for name in (rc[:-2] + "er" if rc.endswith("or") else rc + "s", rc[0].lower() + rc[1:]):
            I.add(t_fail - timedelta(seconds=rng.randint(1, 30)), "ERROR", i, {"worker": other, "thread": other + "-exec-1"},
                  "reservation processing blocked",
                  ["java.lang.IllegalStateException: reservation queue blocked on %s" % other,
                   "Caused by: com.shop.inventory.LedgerLockTimeout: lock ledger-shard-99 held by %s" % name,
                   "    at com.shop.inventory.%s.compact(%s.java:77)" % (name, name)])

    key = {"gateway_request_id": req, "upstream_trace_id": trace, "hold_id": hold, "worker": worker,
           "root_cause_component": rc, "payment_intent_id": pi}
    hidden_vals = {"gateway_status": 502, "orders_timeout_ms": timeout_ms, "hold_queued_at": q_ts,
                   "worker_queue_depth": depth, "lock_name": lock, "payment_status": "canceled"}
    hidden = HIDDEN[:hidden_n]
    for k_, _ in hidden:
        key[k_] = hidden_vals[k_]
    hidden_txt = "".join("%d. `%s` — %s\n" % (7 + j, k_, d) for j, (k_, d) in enumerate(hidden))
    if not hidden_n:
        hidden_txt = "\n7–12. *(hidden)*\n"
    prompt = PROMPT.format(n=6 + hidden_n if hidden_n else 12, order=order, g=g, o=o, i=i, p=p, hidden=hidden_txt)
    return {g: G.text(), o: O.text(), i: I.text(), p: P.text()}, key, prompt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scenarios", default="")
    ap.add_argument("--order", default="ORD-77341")
    ap.add_argument("--hidden", type=int, default=6)
    ap.add_argument("--names", default="gateway,orders,inventory,payments")
    a = ap.parse_args()
    logs, key, prompt = generate(a.seed, set(filter(None, a.scenarios.split(","))), a.order,
                                 a.names.split(","), a.hidden)
    os.makedirs(os.path.join(a.out, "logs"), exist_ok=True)
    for name, text in logs.items():
        with open(os.path.join(a.out, "logs", name + ".log"), "w") as f:
            f.write(text)
    with open(os.path.join(a.out, "answer_key.json"), "w") as f:
        json.dump(key, f, indent=1)
    with open(os.path.join(a.out, "TASK.md"), "w") as f:
        f.write(prompt)


if __name__ == "__main__":
    main()
