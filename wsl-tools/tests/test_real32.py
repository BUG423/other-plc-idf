"""REAL32 编解码往返与契约布局测试（ABCD/BADC 各自）。"""

import math
import struct

import pytest

from e521_regs import (
    CONTRACT_REG_COUNT,
    QUANTITIES,
    WORD_ORDER_ABCD,
    WORD_ORDER_BADC,
    decode_quantities,
    decode_real32,
    encode_quantities,
    encode_real32,
)

# 一组贴近项目语义的量值：Vpu 标幺、SOC 0~1、Pcmd/Qcmd 限幅内
SAMPLE_VALUES = {
    "Vpu": 1.0042, "Pline": 0.4521, "Qline": 0.1833, "loading": 0.4711,
    "SOC": 0.8021, "Pess": 0.3123, "Qess": -0.0521, "Pcmd": 0.3011, "Qcmd": 0.0497,
}


def _f32(v: float) -> bytes:
    """float32 位型（往返相等性按位比较，避开 float64 字面量精度噪声）。"""
    return struct.pack(">f", v)


# =====================【已知位型（钉死字节序语义）】=====================

def test_known_bit_patterns_abcd():
    """1.0 与 -0.3 的 ABCD 拆字必须精确（大端直排：reg0=高字）。"""
    assert encode_real32(1.0, WORD_ORDER_ABCD) == (0x3F80, 0x0000)
    assert decode_real32(0x3F80, 0x0000, WORD_ORDER_ABCD) == 1.0
    # -0.3 = 0xBE99999A -> 高字 0xBE99，低字 0x999A
    assert encode_real32(-0.3, WORD_ORDER_ABCD) == (0xBE99, 0x999A)
    assert _f32(decode_real32(0xBE99, 0x999A, WORD_ORDER_ABCD)) == _f32(-0.3)


def test_known_bit_patterns_badc():
    """BADC 是同一 float 的字交换：两字顺序颠倒。"""
    hi, lo = encode_real32(1.0, WORD_ORDER_ABCD)
    assert encode_real32(1.0, WORD_ORDER_BADC) == (lo, hi)
    assert decode_real32(0x0000, 0x3F80, WORD_ORDER_BADC) == 1.0
    assert decode_real32(0x999A, 0xBE99, WORD_ORDER_BADC) == _f32(-0.3) or \
        _f32(decode_real32(0x999A, 0xBE99, WORD_ORDER_BADC)) == _f32(-0.3)


# =====================【往返一致性】=====================

@pytest.mark.parametrize("order", [WORD_ORDER_ABCD, WORD_ORDER_BADC])
@pytest.mark.parametrize("value", [
    0.0, -0.0, 1.0, 0.91, 0.95, 0.975, 0.98, 0.8, 0.4, -0.3, 220.5, 1e-6,
    123456.789, 2**126, -2**126,
])
def test_roundtrip_single(order, value):
    """单值往返：按位型相等（含 -0.0 与极大/极小常规值）。"""
    w = encode_real32(value, order)
    assert _f32(decode_real32(*w, order)) == _f32(value)


@pytest.mark.parametrize("order", [WORD_ORDER_ABCD, WORD_ORDER_BADC])
def test_roundtrip_all_quantities(order):
    """整表 9 量编解码往返。"""
    words = encode_quantities(SAMPLE_VALUES, order)
    assert len(words) == CONTRACT_REG_COUNT
    back = decode_quantities(words, order)
    for name in SAMPLE_VALUES:
        assert _f32(back[name]) == _f32(SAMPLE_VALUES[name]), name


def test_special_values_nan_inf_survive_roundtrip():
    """nan/inf 按位照编照解（上限把关在上层，不在编解码层）。"""
    for v in (float("inf"), float("-inf"), float("nan")):
        for order in (WORD_ORDER_ABCD, WORD_ORDER_BADC):
            w = encode_real32(v, order)
            back = decode_real32(*w, order)
            assert math.isnan(back) if math.isnan(v) else back == v


def test_orders_are_actually_different():
    """同一字对用错字序应解出不同值（防止两个分支写重）。"""
    words = encode_quantities(SAMPLE_VALUES, WORD_ORDER_ABCD)
    wrong = decode_quantities(words, WORD_ORDER_BADC)
    diffs = sum(1 for name in SAMPLE_VALUES
                if _f32(wrong[name]) != _f32(SAMPLE_VALUES[name]))
    assert diffs >= 7, "ABCD/BADC 两种字序的解码结果必须明显不同"


# =====================【契约布局】=====================

def test_contract_offsets():
    """量-寄存器偏移契约：Vpu@0-1 ... Qcmd@16-17，与 CSV 契约一一对应。"""
    words = encode_quantities(SAMPLE_VALUES, WORD_ORDER_ABCD)
    expected_offsets = {"Vpu": 0, "Pline": 2, "Qline": 4, "loading": 6, "SOC": 8,
                        "Pess": 10, "Qess": 12, "Pcmd": 14, "Qcmd": 16}
    for name, off in expected_offsets.items():
        entry = next(q for q in QUANTITIES if q[0] == name)
        assert entry[1] == off, f"{name} 契约偏移应为 {off}"
        w0, w1 = encode_real32(SAMPLE_VALUES[name], WORD_ORDER_ABCD)
        assert (words[off], words[off + 1]) == (w0, w1)


def test_missing_quantity_defaults_zero():
    """encode_quantities 缺量按 0.0 处理，不抛异常。"""
    words = encode_quantities({"Vpu": 1.0}, WORD_ORDER_ABCD)
    vals = decode_quantities(words, WORD_ORDER_ABCD)
    assert _f32(vals["Vpu"]) == _f32(1.0)
    assert vals["SOC"] == 0.0


# =====================【入参校验】=====================

@pytest.mark.parametrize("order", ["ABCD", "big", "", "abcd ", None])
def test_unknown_word_order_rejected(order):
    """未知字序（含大小写不一致）必须报 ValueError，不允许静默走默认。"""
    with pytest.raises(ValueError):
        encode_real32(1.0, order)
    with pytest.raises(ValueError):
        decode_real32(0x3F80, 0x0000, order)


def test_decode_word_count_guard():
    """字数不足 18 时 decode_quantities 抛 ValueError。"""
    with pytest.raises(ValueError):
        decode_quantities([0] * 17, WORD_ORDER_ABCD)
