"""Modbus toolchain self-test, entirely on 127.0.0.1:15020.
Hand-rolled minimal spec-compliant Modbus TCP slave (FC01/02/03 read-only)
+ pymodbus 3.16.1 client, validating station addressing and ABCD/BADC decoding.
No external device involved."""
import struct, json, threading, socket

HOST, PORT = "127.0.0.1", 15020
VALUES = [220.50, 0.9871, 0.0213, 0.4321, 0.7654, -0.1250, 0.0099, 0.8500, -0.3000]
HR = [0] * 32
for i, v in enumerate(VALUES):  # contract encoding: ABCD big-endian, reg0=hi word
    HR[2 * i], HR[2 * i + 1] = struct.unpack(">HH", struct.pack(">f", v))
CO = [1, 0, 1, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 0, 1]
DI = [0, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0]

def slave():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT)); srv.listen(1)
    conn, _ = srv.accept()
    while True:
        head = conn.recv(7)
        if len(head) < 7: break
        tid, _pid, length, uid = struct.unpack(">HHHB", head)
        req = b""
        while len(req) < length - 1:
            chunk = conn.recv(length - 1 - len(req))
            if not chunk: return
            req += chunk
        fc = req[0]
        addr, cnt = struct.unpack(">HH", req[1:5])
        if fc == 3 and 0 <= addr and addr + cnt <= len(HR):
            data = bytes([2 * cnt]) + b"".join(struct.pack(">H", v) for v in HR[addr:addr + cnt])
        elif fc in (1, 2) and 0 <= addr and addr + cnt <= 16:
            bits = (CO if fc == 1 else DI)[addr:addr + cnt]
            packed = 0
            for j, b in enumerate(bits): packed |= (1 << j) if b else 0
            data = bytes([(cnt + 7) // 8]) + packed.to_bytes((cnt + 7) // 8, "little")
        else:
            data = bytes([0x02, 0x00, addr if addr < 256 else 255])  # illegal data address
            conn.sendall(struct.pack(">HHHB", tid, 0, len(data) + 2, uid) + bytes([fc | 0x80]) + data)
            continue
        conn.sendall(struct.pack(">HHHB", tid, 0, len(data) + 2, uid) + bytes([fc]) + data)
    srv.close()

t = threading.Thread(target=slave, daemon=True); t.start()

from pymodbus.client import ModbusTcpClient

def read_kw(client, fn, **kw):
    for key in ("slave", "device_id"):   # pymodbus >=3.10 renamed slave= to device_id=
        try:
            return fn(**kw, **{key: 1})
        except TypeError:
            continue
    raise RuntimeError("neither slave= nor device_id= accepted")

client = ModbusTcpClient(HOST, port=PORT, timeout=2)
out = {"pymodbus_connect": bool(client.connect()), "station_id": 1}
rr = read_kw(client, client.read_holding_registers, address=0, count=32)
out["fc03_ok"] = not rr.isError()
raw = list(rr.registers) if not rr.isError() else []
out["fc03_raw_registers_0_17"] = raw[:18]
out["decode"] = []
for i, v in enumerate(VALUES):
    w0, w1 = raw[2 * i], raw[2 * i + 1]
    out["decode"].append({
        "pair": [2 * i, 2 * i + 1],
        "ABCD_bigendian": struct.unpack(">f", struct.pack(">HH", w0, w1))[0],
        "BADC_wordswap": struct.unpack(">f", struct.pack(">HH", w1, w0))[0],
        "expected": v,
    })
for name, fn in (("fc01_coils", client.read_coils), ("fc02_discrete", client.read_discrete_inputs)):
    r = read_kw(client, fn, address=0, count=16)
    out[name + "_ok"] = not r.isError()
    out[name + "_bits_0_15"] = list(r.bits[:16]) if not r.isError() else None
client.close()
with open("/home/zss/workspace/py2plc/wsl-probe/modbus_selftest.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
