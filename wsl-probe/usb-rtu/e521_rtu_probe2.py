#!/usr/bin/env python3
"""RTU 探测第二轮：奇偶校验/停止位矩阵收尾（Modbus RTU 标准默认 E1/N2），仅 id=1，仍只读 FC03。"""
import json
import time

from pymodbus.client import ModbusSerialClient

PORT = "/dev/ttyACM0"
COMBOS = [  # (baud, parity, stopbits)
    (9600, "E", 1),
    (9600, "N", 2),
    (19200, "E", 1),
    (115200, "E", 1),
    (115200, "N", 2),
]
OUT = "/home/zss/workspace/py2plc/wsl-probe/usb-rtu/rtu_probe_result_parity.json"

results = []
for baud, parity, stopbits in COMBOS:
    c = ModbusSerialClient(port=PORT, baudrate=baud, bytesize=8, parity=parity,
                           stopbits=stopbits, timeout=0.6)
    try:
        ok = c.connect()
    except Exception as e:
        results.append({"combo": [baud, parity, stopbits], "result": f"connect-exception: {e}"})
        print(f"[{baud} {parity}{stopbits}] connect-exception: {e}")
        continue
    if not ok:
        results.append({"combo": [baud, parity, stopbits], "result": "connect-failed"})
        print(f"[{baud} {parity}{stopbits}] connect-failed")
        continue
    entry = {"combo": [baud, parity, stopbits]}
    try:
        try:
            rr = c.read_holding_registers(address=0, count=18, device_id=1)
        except TypeError:
            rr = c.read_holding_registers(address=0, count=18, slave=1)
        if isinstance(rr, Exception):
            entry["result"] = f"exception: {rr}"
        elif rr is None:
            entry["result"] = "timeout/None"
        elif rr.isError():
            entry["result"] = f"modbus-error: {rr}"  # 有应答即是线索
        else:
            entry["result"] = f"ok regs={rr.registers}"
    except Exception as e:
        entry["result"] = f"exception: {type(e).__name__}: {e}"
    results.append(entry)
    print(f"[{baud} {parity}{stopbits}] {entry['result']}")
    try:
        c.close()
    except Exception:
        pass
    time.sleep(0.1)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"port": PORT, "scope": "READ-ONLY FC03 id=1 parity/stopbit matrix",
               "results": results}, f, ensure_ascii=False, indent=2)
print("saved:", OUT)
