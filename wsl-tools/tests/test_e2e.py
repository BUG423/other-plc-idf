"""monitor <-> virtual 端到端测试：127.0.0.1 起真实从站进程，读回并断言解码值与场景预期一致。

覆盖点：
    1. low_voltage 场景：Vpu 跌破 VON=0.95 时 Qcmd 按简化规则抬升、D18 mode=1、低压线圈置位
    2. 恢复段：Vpu 回到 > VOFF=0.975 后 mode=0、Pcmd/Qcmd 回到基础调度值
    3. --calibrate 端到端：对 ABCD 与 BADC 两种字序的从站都能判对（经 monitor CLI 子进程）
    4. 服务器只读面：写功能码被从站拒绝（异常响应）
"""

import json
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from pymodbus.client import ModbusTcpClient

WSL_TOOLS = Path(__file__).resolve().parents[1]
VIRTUAL = WSL_TOOLS / "virtual_easy521.py"
MONITOR = WSL_TOOLS / "e521_monitor.py"
PY = sys.executable

from e521_regs import decode_quantities  # noqa: E402


# =====================【工具】=====================

def _free_port() -> int:
    """申请一个回环空闲端口（先绑 0 拿号再释放，回环内竞争窗口极小）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class VirtualServer:
    """virtual_easy521.py 子进程封装：等 READY 行，用完终止。"""

    def __init__(self, port: int, extra_args: list[str]):
        self.port = port
        self.proc = subprocess.Popen(
            [PY, "-u", str(VIRTUAL), "--port", str(port)] + extra_args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())
        # 进程退出后自然结束

    def wait_ready(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any("READY" in ln for ln in self.lines):
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


def _connect(port: int, retries: float = 6.0) -> ModbusTcpClient:
    """带重试的客户端连接（从站 READY 后立即连也允许瞬时限流）。"""
    deadline = time.monotonic() + retries
    last_err = None
    while time.monotonic() < deadline:
        client = ModbusTcpClient("127.0.0.1", port=port, timeout=1.5)
        try:
            if client.connect():
                return client
        except Exception as exc:   # noqa: BLE001
            last_err = exc
        client.close()
        time.sleep(0.2)
    raise RuntimeError(f"无法连接 127.0.0.1:{port}: {last_err}")


def _read_frame(client, unit_id=1):
    rr = client.read_holding_registers(address=0, count=19, device_id=unit_id)
    assert not rr.isError(), f"FC03 读取失败: {rr}"
    return list(rr.registers)


def _wait_for(client, predicate, timeout: float, step: float = 0.2):
    """轮询等待场景进入目标相位，返回最后一帧；超时返回 None。"""
    deadline = time.monotonic() + timeout
    frame = None
    while time.monotonic() < deadline:
        frame = _read_frame(client)
        values = decode_quantities(frame, "abcd")
        if predicate(values, frame):
            return values, frame
        time.sleep(step)
    return None


@pytest.fixture()
def low_voltage_server(tmp_path):
    """起 low_voltage 场景从站（快节拍 + 缩短保持段，控制测试时长）。"""
    port = _free_port()
    srv = VirtualServer(port, ["--scenario", "low_voltage", "--tick", "0.05",
                               "--hold-dip", "4.0", "--word-order", "abcd",
                               "--log", str(tmp_path / "vlog.jsonl")])
    assert srv.wait_ready(), "从站未在期限内就绪:\n" + "\n".join(srv.lines[-30:])
    yield srv, port
    srv.stop()


# =====================【用例】=====================

def test_e2e_low_voltage_dip_and_recover(low_voltage_server):
    """低压跌落段与恢复段的解码值与场景预期一致。"""
    _srv, port = low_voltage_server
    client = _connect(port)
    try:
        # 1) 等待跌入低压：Vpu < 0.93（<VON=0.95，取阈值留出运动裕量）
        hit = _wait_for(client, lambda v, f: v["Vpu"] < 0.93, timeout=15.0)
        assert hit is not None, "15s 内未观察到低压跌落段"
        values, frame = hit
        mode = frame[18]
        assert mode == 1, f"低压段 D18 mode 应为 1，实际 {mode}"
        # 简化规则：0.91pu 时 Qcmd≈GQ*(VREF-Vpu)=0.35（限幅 QMAX=0.4 内）
        assert 0.25 <= values["Qcmd"] <= 0.40, f"低压段 Qcmd 异常: {values['Qcmd']}"
        assert values["Pcmd"] == 0.0, "无功未饱和时低压段 Pcmd 应为 0"
        assert 0.0 <= values["SOC"] <= 1.0

        # 低压线圈（FC01 bit1）应置位
        rc = client.read_coils(address=0, count=8, device_id=1)
        assert not rc.isError()
        assert rc.bits[1] is True, "低压模式线圈 bit1 应置位"

        # 2) 等待恢复：Vpu > VOFF=0.975 后 mode=0，Pcmd/Qcmd 回基础调度值
        back = _wait_for(client, lambda v, f: v["Vpu"] > 0.975 and f[18] == 0,
                         timeout=18.0)
        assert back is not None, "18s 内未观察到恢复段"
        values, frame = back
        assert abs(values["Pcmd"] - 0.30) < 0.02, f"恢复段 Pcmd 应为基础调度 0.30: {values['Pcmd']}"
        assert abs(values["Qcmd"] - 0.05) < 0.02, f"恢复段 Qcmd 应为基础调度 0.05: {values['Qcmd']}"
    finally:
        client.close()


def test_e2e_server_is_read_only(low_voltage_server):
    """从站只读面：写保持寄存器/线圈必须被拒绝（异常响应），读不受影响。"""
    _srv, port = low_voltage_server
    client = _connect(port)
    try:
        rw = client.write_register(address=0, value=0x3F80, device_id=1)
        assert rw.isError(), "服务器应拒绝 FC06 写（readonly 数据面）"
        wc = client.write_coil(address=0, value=True, device_id=1)
        assert wc.isError(), "服务器应拒绝 FC05 写"
        rr = client.read_holding_registers(address=0, count=18, device_id=1)
        assert not rr.isError(), "写被拒后读必须仍正常"
    finally:
        client.close()


def test_e2e_jsonl_log_written(low_voltage_server, tmp_path):
    """--log 的 JSONL 应包含时间戳、场景与寄存器快照。"""
    srv, port = low_voltage_server
    client = _connect(port)
    try:
        time.sleep(1.2)   # 让播放器至少写出 1 条日志
    finally:
        client.close()
    srv.stop()            # 先停流再断言文件
    log_file = tmp_path / "vlog.jsonl"
    assert log_file.exists() and log_file.stat().st_size > 0
    row = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    for key in ("ts", "scenario", "registers", "values", "mode"):
        assert key in row, f"JSONL 缺字段 {key}"
    assert row["scenario"] == "low_voltage"
    assert len(row["registers"]) == 19


def test_e2e_calibrate_detects_abcd(tmp_path):
    """monitor CLI --once --calibrate 连 ABCD 从站：判对字序并落盘 JSONL。"""
    port = _free_port()
    srv = VirtualServer(port, ["--scenario", "steady", "--tick", "0.1",
                               "--word-order", "abcd"])
    assert srv.wait_ready(), "\n".join(srv.lines[-30:])
    try:
        jsonl = tmp_path / "m.jsonl"
        proc = subprocess.run(
            [PY, str(MONITOR), "--host", "127.0.0.1", "--port", str(port),
             "--once", "--calibrate", "--jsonl", str(jsonl)],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"monitor 退出码异常:\n{proc.stdout}\n{proc.stderr}"
        assert "abcd" in proc.stdout.lower()
        rows = [json.loads(ln) for ln in
                jsonl.read_text(encoding="utf-8").splitlines()]
        assert rows and rows[0]["word_order"] == "abcd"
        # 与从站快照核对：Vpu 必须在 1.0 附近（steady 缓波纹幅值 0.01）
        assert abs(rows[0]["values"]["Vpu"] - 1.0) <= 0.02
        assert abs(rows[0]["values"]["Pcmd"] - 0.30) <= 0.02
    finally:
        srv.stop()


def test_e2e_calibrate_detects_badc(tmp_path):
    """monitor CLI --once --calibrate 连 BADC（字交换）从站：判别为 badc。"""
    port = _free_port()
    srv = VirtualServer(port, ["--scenario", "steady", "--tick", "0.1",
                               "--word-order", "badc"])
    assert srv.wait_ready(), "\n".join(srv.lines[-30:])
    try:
        jsonl = tmp_path / "m.jsonl"
        proc = subprocess.run(
            [PY, str(MONITOR), "--host", "127.0.0.1", "--port", str(port),
             "--once", "--calibrate", "--jsonl", str(jsonl)],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"monitor 退出码异常:\n{proc.stdout}\n{proc.stderr}"
        rows = [json.loads(ln) for ln in
                jsonl.read_text(encoding="utf-8").splitlines()]
        assert rows and rows[0]["word_order"] == "badc", \
            f"应判别为 badc:\n{proc.stdout[-2000:]}"
        # 按 badc 解码后 Vpu 仍应 ≈1.0 —— 字序判对，数值才对
        assert abs(rows[0]["values"]["Vpu"] - 1.0) <= 0.02
    finally:
        srv.stop()


def test_e2e_overload_scenario():
    """overload 场景：loading 越限（>1.0）时 mode 进入 2（正向），Pcmd 方向为正。"""
    port = _free_port()
    srv = VirtualServer(port, ["--scenario", "overload", "--tick", "0.05"])
    assert srv.wait_ready(), "\n".join(srv.lines[-30:])
    client = _connect(port)
    try:
        hit = _wait_for(client, lambda v, f: f[18] in (2, 3), timeout=25.0)
        assert hit is not None, "25s 内未观察到过载段"
        values, frame = hit
        mode = frame[18]
        assert values["loading"] > 1.0, f"过载段 loading 应 >1.0: {values['loading']}"
        if mode == 2:
            assert values["Pline"] > 0 and values["Pcmd"] > 0, "正向过载：Pline>0 且 Pcmd>0"
        else:
            assert values["Pline"] < 0 and values["Pcmd"] < 0, "反向过载：Pline<0 且 Pcmd<0"
    finally:
        client.close()
        srv.stop()


@pytest.mark.parametrize("scenario", ["steady", "low_voltage", "overload"])
def test_e2e_monitor_quality_metadata_for_all_scenarios(tmp_path, scenario):
    """Real loopback server/client preserve transport facts without inferring PLC state."""
    port = _free_port()
    srv = VirtualServer(port, ["--scenario", scenario, "--quiet"])
    assert srv.wait_ready(), "\n".join(srv.lines[-30:])
    try:
        output = tmp_path / "quality.jsonl"
        proc = subprocess.run(
            [PY, str(MONITOR), "--host", "127.0.0.1", "--port", str(port),
             "--once", "--calibrate", "--project-revision", "source-test-123",
             "--jsonl", str(output)], capture_output=True, text=True, timeout=15)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        row, = [json.loads(line) for line in output.read_text().splitlines()]
        assert row["quality"]["read"] == "ok"
        assert row["word_order_source"] == "calibrated"
        assert row["quality"]["word_order"] == "heuristic"
        assert row["registers"] == row["raw"]["holding_registers"]
        assert row["project_revision"] == "source-test-123"
        assert row["quality"]["program_match"] == "unverified"
        assert row["quality"]["plc_scan_progress"] == "unverified"
        assert row["quality"]["freshness"] == "unverified"
        assert row["read_duration_ms"] >= 0 and len(row["contract_sha256"]) == 64
    finally:
        srv.stop()
