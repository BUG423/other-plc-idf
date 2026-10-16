"""e521_regs.py — py2plc 项目 Modbus 寄存器契约（唯一定义处）。

被 virtual_easy521.py（虚拟从站）与 e521_monitor.py（监测客户端）共同引用，
保证两端对 REAL32 编解码、字序、量程的理解一字不差。

契约来源：
    plc/easy521/modbus_register_map_real32.csv
        FC03 保持寄存器 0~17，REAL32 每量占 2 寄存器；
        字序契约 ABCD（大端，reg0=高字），官方无记载，BADC（字交换）为备选，
        因此工具链必须同时支持两种字序并可自动判别。
算法阈值参考：
    modbus_realtime.py 的 CONTROLLER_P（VON/VOFF/VREF/LON/LTARGET/PMAX/QMAX 等）。

Python 3.12，仅依赖标准库。
"""

import math
import struct
import sys

# =====================【字序常量】=====================

WORD_ORDER_ABCD = "abcd"   # 大端直排：reg0=高字，reg1=低字（项目契约默认）
WORD_ORDER_BADC = "badc"   # 字交换：reg0=低字，reg1=高字（备选字序）
WORD_ORDERS = (WORD_ORDER_ABCD, WORD_ORDER_BADC)


# =====================【量定义表】=====================
# (量名, 寄存器偏移, 中文说明, 单位, 合理范围下限, 合理范围上限)
# 合理范围用于 --calibrate 字序自动判别的打分，也用于监测输出标记；
# 阈值语义对齐 modbus_realtime.py：Vpu 标幺、Pcmd/Qcmd 限幅 ±0.8/±0.4 pu。

QUANTITIES = (
    ("Vpu",     0,  "节点电压标幺值",       "pu",  0.5,  1.5),
    ("Pline",   2,  "馈线有功功率",         "pu",  -10.0, 10.0),
    ("Qline",   4,  "馈线无功功率",         "pu",  -10.0, 10.0),
    ("loading", 6,  "线路负载率",           "-",   0.0,  20.0),
    ("SOC",     8,  "储能荷电状态",         "-",   0.0,  1.0),
    ("Pess",    10, "储能实测有功功率",     "pu",  -10.0, 10.0),
    ("Qess",    12, "储能实测无功功率",     "pu",  -10.0, 10.0),
    ("Pcmd",    14, "储能有功指令",         "pu",  -2.0,  2.0),
    ("Qcmd",    16, "储能无功指令",         "pu",  -2.0,  2.0),
)

#: 契约寄存器总数（9 量 x 2 寄存器）
CONTRACT_REG_COUNT = 18

#: 量名 -> (偏移, 下限, 上限)，供打分与标记快速查表
_ENVELOPE = {name: (lo, hi) for name, _off, _cn, _unit, lo, hi in QUANTITIES}


# =====================【REAL32 编解码】=====================

def encode_real32(value: float, word_order: str = WORD_ORDER_ABCD) -> tuple[int, int]:
    """按指定字序把 float 编码为 (寄存器0, 寄存器1) 两个 uint16。

    ABCD：reg0=IEEE-754 高 16 位；BADC：reg0=低 16 位。
    非有限输入（inf/nan）按位照编——上层自行把关。
    """
    if word_order not in WORD_ORDERS:
        raise ValueError(f"未知字序: {word_order!r}（可选 {WORD_ORDERS}）")
    w_hi, w_lo = struct.unpack(">HH", struct.pack(">f", float(value)))
    if word_order == WORD_ORDER_ABCD:
        return (w_hi, w_lo)
    return (w_lo, w_hi)


def decode_real32(w0: int, w1: int, word_order: str = WORD_ORDER_ABCD) -> float:
    """按指定字序把两个 uint16 寄存器解码为 float。

    与 encode_real32 严格互逆：decode(encode(v)) == v（含 -0.0 与 nan 位型）。
    """
    if word_order not in WORD_ORDERS:
        raise ValueError(f"未知字序: {word_order!r}（可选 {WORD_ORDERS}）")
    hi, lo = (w0, w1) if word_order == WORD_ORDER_ABCD else (w1, w0)
    return struct.unpack(">f", struct.pack(">HH", hi & 0xFFFF, lo & 0xFFFF))[0]


def decode_quantities(words: list[int], word_order: str = WORD_ORDER_ABCD) -> dict[str, float]:
    """把 >=18 个寄存器字按契约解成 {量名: 数值}。字数不足抛 ValueError。"""
    if len(words) < CONTRACT_REG_COUNT:
        raise ValueError(f"寄存器字数不足: {len(words)} < {CONTRACT_REG_COUNT}")
    out: dict[str, float] = {}
    for name, off, _cn, _unit, _lo, _hi in QUANTITIES:
        out[name] = decode_real32(words[off], words[off + 1], word_order)
    return out


def encode_quantities(values: dict[str, float], word_order: str = WORD_ORDER_ABCD) -> list[int]:
    """把 {量名: 数值} 按契约编成 18 个寄存器字；缺量按 0.0 处理。"""
    words: list[int] = [0] * CONTRACT_REG_COUNT
    for name, off, _cn, _unit, _lo, _hi in QUANTITIES:
        w0, w1 = encode_real32(values.get(name, 0.0), word_order)
        words[off] = w0
        words[off + 1] = w1
    return words


# =====================【字序自动判别】=====================
# 判据（对每个量在两种字序下分别打分，满分 9）：
#   强通过 1.0：有限、非"荒谬微小值"、落在契约合理范围内；
#   弱通过 0.5：恰好为 0.0（零值两可——(0,0) 字对两种字序解出相同结果）；
#   不通过 0.0：非有限 / 非零微小值（含 float32 次正规）/ 超出合理范围。
# "荒谬微小"指 0<|v|<1e-4：契约内任何量的真实非零值都远大于 1e-4（最小量级为
# Qcmd 基线 0.05 量级），而字交换垃圾常落在 1e-45~1e-20（低位字的尾数位错当指数位）。
# 错误字序下其余垃圾为 1e±20 以外量级或 NaN，与正确字序（9/9 强通过）区分度极高。
# 得分并列时无法判别，默认 ABCD 并如实报告。

#: 非零值的可信下限：契约各量真实非零值均 >= ~5e-3，低于此按字序垃圾处理
MIN_PLAUSIBLE_ABS = 1e-4


def _score_one(name: str, value: float) -> float:
    """单个量在当前字序下的得分（1.0 / 0.5 / 0.0）。"""
    if not math.isfinite(value):
        return 0.0
    if value == 0.0:
        return 0.5  # 零值两可
    if abs(value) < MIN_PLAUSIBLE_ABS:
        return 0.0  # 非零微小值（含 float32 次正规）——几乎必然是字序垃圾
    lo, hi = _ENVELOPE[name]
    return 1.0 if lo <= value <= hi else 0.0


def calibrate_word_order(words: list[int]) -> dict:
    """对一组寄存器字做字序自动判别。

    返回:
        {
          "best": "abcd"|"badc",        # 得分最高者；并列时默认 abcd
          "decided": bool,              # True=两序得分有明确差距；False=并列（含全零/全弱通过）
          "scores": {"abcd": 分, "badc": 分},
          "detail": {量名: {"abcd": 值, "badc": 值, "abcd_ok": bool, "badc_ok": bool}},
        }
    """
    if len(words) < CONTRACT_REG_COUNT:
        raise ValueError(f"寄存器字数不足: {len(words)} < {CONTRACT_REG_COUNT}")
    scores: dict[str, float] = {}
    detail: dict[str, dict] = {}
    for order in WORD_ORDERS:
        total = 0.0
        for name, off, _cn, _unit, _lo, _hi in QUANTITIES:
            v = decode_real32(words[off], words[off + 1], order)
            s = _score_one(name, v)
            total += s
            detail.setdefault(name, {})[order] = v
            detail[name][f"{order}_ok"] = s >= 1.0
        scores[order] = total
    best = WORD_ORDER_ABCD
    decided = False
    if scores[WORD_ORDER_BADC] > scores[WORD_ORDER_ABCD]:
        best = WORD_ORDER_BADC
    if scores[WORD_ORDER_ABCD] != scores[WORD_ORDER_BADC]:
        decided = True
    return {"best": best, "decided": decided, "scores": scores, "detail": detail}


# =====================【量程标记】=====================

def in_range(name: str, value: float) -> bool:
    """量值是否落在契约合理范围内（仅提示用，不参与判别打分）。"""
    lo, hi = _ENVELOPE[name]
    return lo <= value <= hi


def describe_order(word_order: str) -> str:
    """字序的人类可读说明（表格/日志用）。"""
    if word_order == WORD_ORDER_ABCD:
        return "ABCD（大端直排，reg0=高字，项目契约）"
    if word_order == WORD_ORDER_BADC:
        return "BADC（字交换，reg0=低字，备选）"
    return f"未知字序 {word_order!r}"


if __name__ == "__main__":
    # 自检：编解码往返 + 判别冒烟，python e521_regs.py 直接可跑
    sample = [1.0042, 0.4521, 0.1833, 0.4711, 0.8021, 0.3123, -0.0521, 0.3011, 0.0497]
    for order in WORD_ORDERS:
        words = encode_quantities(dict(zip([q[0] for q in QUANTITIES], sample)), order)
        back = decode_quantities(words, order)
        for name, v0, v1 in zip([q[0] for q in QUANTITIES], sample, back.values()):
            # 按位型比较：float32 解出的是最近可表示值，与 float64 字面量未必逐位相等
            assert struct.pack(">f", v0) == struct.pack(">f", v1), \
                f"{order} 往返失败: {name} {v0} != {v1}"
        rep = calibrate_word_order(words)
        assert rep["best"] == order and rep["decided"], f"{order} 判别失败: {rep}"
        print(f"[自检] 字序 {order}: 往返 OK, 自动判别 -> {rep['best']} "
              f"(abcd={rep['scores']['abcd']}, badc={rep['scores']['badc']})")
    print("[自检] e521_regs 契约模块 OK", file=sys.stderr)
