#!/usr/bin/env python3
"""Easy521 USB CDC 口 Modbus RTU 只读探测。

背景：usbipd 已将 PLC (VID_0471/PID_A531) 挂入 WSL，内核 cdc_acm 认领为 /dev/ttyACM0。
本脚本回答一个问题：固件是否在编程口(CDC 串口)上应答标准 Modbus RTU 读帧。

⚠️ 安全红线：只发 FC03 读保持寄存器(地址 0~17)，无任何写操作。
"""
import json
import struct
import time

from pymodbus.client import ModbusSerialClient

PORT = "/dev/ttyACM0"
BAUDS = [115200, 57600, 38400, 19200, 9600]
DEVIDS = [1, 2]
OUT = "/home/zss/workspace/py2plc/wsl-probe/usb-rtu/rtu_probe_result.json"

# 项目寄存器契约：FC03 0~17，REAL32 每量 2 寄存器
NAMES = ["Vpu", "Pline", "Qline", "loading", "SOC", "Pess", "Qess", "Pcmd", "Qcmd"]


def decode(regs):
    """两种字序都解出来，供人工判读。"""
    out = {}
    for order in ("ABCD", "BADC"):
        vals = {}
        for i, nm in enumerate(NAMES):
            b0, b1 = regs[2 * i], regs[2 * i + 1]
            raw = struct.pack(">HH", b0, b1) if order == "ABCD" else struct.pack(">HH", b1, b0)
            vals[nm] = struct.unpack(">f", raw)[0]
        out[order] = vals
    return out


def read_regs(client, devid, address, count):
    """pymodbus 3.16 用 device_id，旧参数名 slave 做兜底。异常也原样返回。"""
    try:
        try:
            return client.read_holding_registers(address=address, count=count, device_id=devid)
        except TypeError:
            return client.read_holding_registers(address=address, count=count, slave=devid)
    except Exception as e:  # 串口层异常也记录
        return e


def main():
    results = []
    port_ok = False
    for baud in BAUDS:
        for devid in DEVIDS:
            c = ModbusSerialClient(port=PORT, baudrate=baud, bytesize=8, parity="N",
                                   stopbits=1, timeout=0.6)
            try:
                ok = c.connect()
            except Exception as e:
                results.append({"baud": baud, "devid": devid, "result": f"connect-exception: {e}"})
                print(f"[{baud:>6} id{devid}] connect-exception: {e}")
                continue
            if not ok:
                results.append({"baud": baud, "devid": devid, "result": "connect-failed"})
                print(f"[{baud:>6} id{devid}] connect-failed")
                continue
            port_ok = True
            entry = {"baud": baud, "devid": devid}
            rr = read_regs(c, devid, 0, 18)
            if isinstance(rr, Exception):
                entry["result"] = f"exception: {type(rr).__name__}: {rr}"
            elif rr is None:
                entry["result"] = "timeout/None"
            elif rr.isError():
                # 设备应答了 Modbus 异常帧——仍是"设备在听"的证据
                entry["result"] = f"modbus-error: {rr}"
            else:
                entry["result"] = "ok"
                entry["regs_raw"] = rr.registers
                entry["decode"] = decode(rr.registers)
            results.append(entry)
            print(f"[{baud:>6} id{devid}] {entry['result']}")
            try:
                c.close()
            except Exception:
                pass
            time.sleep(0.15)

    summary = {
        "port": PORT,
        "port_openable": port_ok,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "READ-ONLY FC03 addr 0-17, devid 1/2, baud scan",
        "results": results,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
