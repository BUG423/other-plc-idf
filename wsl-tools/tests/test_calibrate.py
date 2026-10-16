"""字序自动判别（--calibrate 的判据核心）测试。"""

import pytest

from e521_regs import (
    WORD_ORDER_ABCD,
    WORD_ORDER_BADC,
    calibrate_word_order,
    encode_quantities,
    in_range,
)

SAMPLE_VALUES = {
    "Vpu": 1.0042, "Pline": 0.4521, "Qline": 0.1833, "loading": 0.4711,
    "SOC": 0.8021, "Pess": 0.3123, "Qess": -0.0521, "Pcmd": 0.3011, "Qcmd": 0.0497,
}


# =====================【正确判别】=====================

def test_detect_abcd_when_encoded_abcd():
    """ABCD 编码的数据必须判为 abcd 且有明确结论。"""
    rep = calibrate_word_order(encode_quantities(SAMPLE_VALUES, WORD_ORDER_ABCD))
    assert rep["best"] == WORD_ORDER_ABCD
    assert rep["decided"] is True
    assert rep["scores"]["abcd"] == 9.0
    assert rep["scores"]["badc"] < 5.0   # 错误字序大量失分
    # 正确字序下每量都应强通过
    for name, detail in rep["detail"].items():
        assert detail["abcd_ok"] is True, name


def test_detect_badc_when_encoded_badc():
    """BADC 编码的数据必须判为 badc——判别器必须对称。"""
    rep = calibrate_word_order(encode_quantities(SAMPLE_VALUES, WORD_ORDER_BADC))
    assert rep["best"] == WORD_ORDER_BADC
    assert rep["decided"] is True
    assert rep["scores"]["badc"] == 9.0
    assert rep["scores"]["abcd"] < 5.0


@pytest.mark.parametrize("vpu,pl,ld,soc", [
    (1.0, 0.45, 0.48, 0.80),    # 全部整值附近（低位字多为 0，最容易骗过判别器）
    (0.99, 0.44, 0.47, 0.79),
    (1.01, 0.61, 1.28, 0.50),   # 过载数据形态
    (0.91, 0.42, 0.46, 0.75),   # 低压数据形态
])
def test_detect_robust_across_scenarios(vpu, pl, ld, soc):
    """跨场景形态的判别稳健性：即使是 2 的幂次附近的值也能正确判别。"""
    values = dict(SAMPLE_VALUES, Vpu=vpu, Pline=pl, loading=ld, SOC=soc)
    rep_a = calibrate_word_order(encode_quantities(values, WORD_ORDER_ABCD))
    assert rep_a["best"] == WORD_ORDER_ABCD and rep_a["decided"]
    rep_b = calibrate_word_order(encode_quantities(values, WORD_ORDER_BADC))
    assert rep_b["best"] == WORD_ORDER_BADC and rep_b["decided"]


# =====================【退化输入】=====================

def test_all_zero_registers_inconclusive_default_abcd():
    """全零寄存器（PLC 刚上电/未播放场景）无法判别：默认 abcd 且 decided=False。"""
    rep = calibrate_word_order([0] * 18)
    assert rep["decided"] is False
    assert rep["best"] == WORD_ORDER_ABCD
    assert rep["scores"]["abcd"] == rep["scores"]["badc"]


def test_garbage_registers_not_confidently_wrong():
    """随机垃圾字：判别器不崩溃；若判别则判据可解释（分数有限、非负）。"""
    import random
    rng = random.Random(20261016)
    for _ in range(50):
        words = [rng.randrange(0x10000) for _ in range(18)]
        rep = calibrate_word_order(words)
        assert rep["best"] in (WORD_ORDER_ABCD, WORD_ORDER_BADC)
        assert rep["scores"]["abcd"] >= 0.0 and rep["scores"]["badc"] >= 0.0
        assert rep["scores"]["abcd"] <= 9.0 and rep["scores"]["badc"] <= 9.0


def test_one_quantity_swapped_still_detects_majority():
    """个别量损坏（如某量字对被改写）不应翻转整体结论——判据按多数打分。"""
    words = encode_quantities(SAMPLE_VALUES, WORD_ORDER_ABCD)
    words[8], words[9] = 0xDEAD, 0xBEEF   # 破坏 SOC 字对
    rep = calibrate_word_order(words)
    assert rep["best"] == WORD_ORDER_ABCD
    assert rep["scores"]["abcd"] > rep["scores"]["badc"]


def test_word_count_guard():
    with pytest.raises(ValueError):
        calibrate_word_order([0] * 10)


# =====================【量程标记（表格 OUT-OF-RANGE 提示用）】=====================

def test_in_range_flags():
    assert in_range("SOC", 0.5) is True
    assert in_range("SOC", 1.2) is False
    assert in_range("Vpu", 0.91) is True     # 0.91 在 [0.5,1.5] 内（低压但合理）
    assert in_range("loading", 1.28) is True  # 过载仍在合理范围（量程放宽到 20）
    assert in_range("Pcmd", 0.8) is True
    assert in_range("Pcmd", 2.5) is False
