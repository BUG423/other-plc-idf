"""WSL -> Windows host TCP path test (read-only connects, banner read)."""
import socket, json, time

TARGETS = [
    ("172.18.96.1", 5020, "WSL gateway = Windows vEthernet (WSL)"),
    ("172.18.96.1", 5021, "gateway closed-port baseline"),
    ("10.185.16.82", 5020, "Windows WLAN physical NIC"),
]
results = []
for ip, port, note in TARGETS:
    t0 = time.time()
    rec = {"target": f"{ip}:{port}", "note": note}
    try:
        s = socket.create_connection((ip, port), timeout=2.0)
        rec["connect"] = "OK"
        rec["rtt_ms"] = round((time.time() - t0) * 1000, 2)
        s.settimeout(2.0)
        try:
            data = s.recv(64)
            rec["banner"] = data.decode("ascii", "replace")
        except socket.timeout:
            rec["banner"] = "(no banner within 2s)"
        s.close()
    except ConnectionRefusedError:
        rec["connect"] = "REFUSED (host reachable, port closed -> RST came back)"
    except socket.timeout:
        rec["connect"] = "TIMEOUT (filtered, likely firewall drop)"
    except OSError as e:
        rec["connect"] = f"ERROR: {e}"
    results.append(rec)
    print(json.dumps(rec, ensure_ascii=False))

with open("/home/zss/workspace/py2plc/wsl-probe/tcp_path_test.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("saved -> tcp_path_test.json")
