"""Read-only TCP connect scan of 10.185.16.0/24 on a fixed, limited port set.
No service/OS fingerprinting: a hit only records that the TCP port accepted a connect."""
import socket, json, time
from concurrent.futures import ThreadPoolExecutor

NET = "10.185.16"
PORTS = [502, 80, 8080, 4840, 1217, 2455, 20256, 20257, 44818]
TIMEOUT = 0.4
WORKERS = 200

def probe(args):
    ip, port = args
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((ip, port))
        return {"ip": ip, "port": port, "open": True}
    except (socket.timeout, ConnectionRefusedError, OSError):
        return None
    finally:
        try: s.close()
        except OSError: pass

targets = [(f"{NET}.{h}", p) for h in range(1, 255) for p in PORTS]
t0 = time.monotonic()
hits = []
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for r in ex.map(probe, targets):
        if r:
            hits.append(r)
            print("OPEN:", r["ip"], r["port"], flush=True)

out = {
    "scan_meta": {
        "method": "TCP connect only, no fingerprinting",
        "range": f"{NET}.1-{NET}.254",
        "ports": PORTS,
        "connect_timeout_s": TIMEOUT,
        "workers": WORKERS,
        "duration_s": round(time.monotonic() - t0, 1),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    },
    "open": hits,
}
with open("/home/zss/workspace/py2plc/wsl-probe/plc_discover.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"done in {out['scan_meta']['duration_s']}s, {len(hits)} open port(s)")
