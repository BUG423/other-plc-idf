from pymodbus.client import ModbusTcpClient   # 导入pymodbus的TCP客户端，实现modbus‑tcp主站通讯
import struct                                  # 二进制字节打包解包库，用来实现浮点数<‑>modbus寄存器转换
import tkinter as tk                           # 导入tkinter，GUI图形界面库
from tkinter import ttk                        # tkinter的美化控件库
import math                                    # 数学库，提供sqrt、hypot等数学计算
import csv
import time

# =====================【嵌入控制器部分 开始】=====================
def control(Vpu, Pline, Qline, loading, SOC, Pess, Qess, state, p):
    """
    Measurement‑driven adaptive supervisor (Python port from Simulink Matlab‑function)
    移植自Simulink matlab‑function：测量驱动自适应储能上层控制器
    Modes工作模式:
        0 normal        正常模式
        1 low voltage   低压模式
        2 forward overload 正向过载
        3 reverse overload 反向过载

    Inputs输入:
        Vpu:  节点电压标幺值
        Pline: 配网线路有功功率（线路侧）
        Qline: 配网线路无功功率（线路侧）
        loading: 线路当前负载率
        SOC: 储能电池荷电状态 0~1
        Pess: 储能当前实际有功功率
        Qess: 储能当前实际无功功率
        state: list，**延迟后的控制器状态向量(20维)**，模拟Simulink 1/z延迟模块输出
        p: list，全局参数向量，完全对齐原Matlab p数组

    Returns返回:
        Pcmd, Qcmd, mode, stateNext, sVPout, sVQout, kPout, kQout, confidence
        Pcmd：储能有功输出指令
        Qcmd：储能无功输出指令
        mode：系统工作模式
        stateNext：本步计算得到新状态，输出给到Simulink的1/z延迟模块输入端
        sVPout/sVQout：辨识得到电压‑功率灵敏度
        kPout/kQout：线路‑储能耦合系数
        confidence：辨识置信度0~1
    """

    # ========= 参数读取 (Matlab p(2)~p(35)，Python list下标从0，p[1]对应原p(2)) =========
    VON       = p[1]      # 低压模式投入电压阈值，Vpu低于此值进入低压保护模式
    VOFF      = p[2]      # 低压模式退出电压阈值
    VREF      = p[3]      # 电压控制参考标幺值
    LON       = p[4]      # 过载模式投入负载率阈值
    LOFF      = p[5]      # 过载模式退出负载率阈值
    LTARGET   = p[6]      # 线路目标负载率
    PMAX      = p[7]      # 储能有功功率最大限值
    QMAX      = p[8]      # 储能无功功率最大限值
    SOCMIN    = p[9]      # 储能SOC下限
    SOCMAX    = p[10]     # 储能SOC上限
    SMARGIN   = p[11]     # SOC裕度，避免刚好卡在SOCMIN/SOCMAX边界震荡
    QREACH    = p[12]     # 无功达到QMAX多少比例后锁存，切换有功调压
    DWELL     = p[13]     # 无功饱和锁存延时计数（周期数）
    RECOVER   = p[14]     # 低压恢复需要的连续正常电压计数（周期数）
    GQ        = p[15]     # 无功调压增益
    GP        = p[16]     # 有功调压增益
    ALPHA     = p[19]     # 电压灵敏度sVP/sVQ辨识的遗忘因子(0~1)，越大越信任新样本
    DMIN      = p[20]     # 功率变化最小阈值；变化太小不做辨识，避免噪声
    WINDOW    = p[21]     # 辨识窗口周期数；累计WINDOW步执行一次辨识
    DOM       = p[22]     # 主导判据系数；区分有功主导扰动 / 无功主导扰动
    SVPMIN    = p[23]     # 辨识sVP下限限幅
    SVPMAX    = p[24]     # 辨识sVP上限限幅
    SVQMIN    = p[25]     # 辨识sVQ下限限幅
    SVQMAX    = p[26]     # 辨识sVQ上限限幅
    DVMAX     = p[27]     # 窗口内电压最大允许变化量；电压跳变过大样本丢弃
    KALPHA    = p[30]     # kP/kQ辨识遗忘因子
    KMIN      = p[31]     # kP/kQ辨识下限限幅
    KMAX      = p[32]     # kP/kQ辨识上限限幅
    CINC      = p[33]     # 辨识置信度计数器每成功辨识一次的增量
    EPS       = p[34]     # 极小值，防止除0，浮点数判0

    # ========= 解析状态向量 state（来自1/z延迟模块输出，20维） =========
    low             = state[0]      # 0/1标志位，1=处于低压模式
    recoverCount    = state[1]      # 低压恢复连续计数
    direction       = state[2]      # 过载方向：0无过载，1正向过载，-1反向过载
    uvCount         = state[3]      # 无功饱和延时计数器
    qLatched        = state[4]      # 无功饱和锁存标志；1代表无功已经打满上限，切有功调压
    anchorV         = state[5]      # 辨识窗口锚点：记录窗口起始时刻电压
    anchorPline     = state[6]      # 辨识窗口锚点：窗口起始线路有功
    anchorQline     = state[7]      # 辨识窗口锚点：窗口起始线路无功
    anchorPess      = state[8]      # 辨识窗口锚点：窗口起始储能有功
    anchorQess      = state[9]      # 辨识窗口锚点：窗口起始储能无功
    estimatorCount  = state[10]     # 辨识窗口计数器，累计步数
    sVP             = state[11]     # 辨识参数 dV / dPess 电压对储能有功灵敏度
    sVQ             = state[12]     # 辨识参数 dV / dQess 电压对储能无功灵敏度
    kP              = state[13]     # 耦合系数 dPline / dPess；储能变化多少，线路会变化多少
    kQ              = state[14]     # 耦合系数 dQline / dQess
    cVP             = state[15]     # sVP辨识成功计数(置信度) 0~1
    cVQ             = state[16]     # sVQ辨识成功计数
    cKP             = state[17]     # kP辨识成功计数
    cKQ             = state[18]     # kQ辨识成功计数
    initialized     = state[19]     # 初始化标志位，0未初始化；1已经初始化完成

    # -------- 初始化逻辑，第一次进入函数执行 --------
    if initialized < 0.5:
        anchorV      = Vpu         # 当前电压作为锚点电压
        anchorPline  = Pline       # 当前线路有功锚点
        anchorQline  = Qline       # 当前线路无功锚点
        anchorPess   = Pess        # 当前储能有功锚点
        anchorQess   = Qess        # 当前储能无功锚点
        estimatorCount = 0.0       # 辨识计数器清零
        sVP          = p[17]       # sVP初始值，对应matlab p(18)
        sVQ          = p[18]       # sVQ初始值，对应matlab p(19)
        kP           = p[28]       # kP初始值，对应matlab p(29)
        kQ           = p[29]       # kQ初始值，对 应matlab p(30)
        initialized  = 1.0         # 标记已经初始化，下一轮不再进入
    else:
        estimatorCount = estimatorCount + 1.0   # 每一步辨识窗口计数器+1
        if estimatorCount >= WINDOW:             # 累计到窗口长度，执行一次辨识计算
            # 计算锚点时刻到当前时刻各物理量变化量delta
            dV   = Vpu - anchorV
            dPL  = Pline - anchorPline
            dQL  = Qline - anchorQline
            dP   = Pess - anchorPess
            dQ   = Qess - anchorQess

            # 辨识sVQ：电压对储能无功灵敏度 dV/dQess
            if abs(dV) <= DVMAX and abs(dQ) >= DMIN and abs(dQ) >= DOM * abs(dP):
                sample = (dV - sVP * dP) / dQ   # 剔除有功扰动带来电压，得到dV/dQess样本
                if SVQMIN <= sample <= SVQMAX:  # 样本在合理上下限内，才接收更新
                    sVQ = (1.0 - ALPHA) * sVQ + ALPHA * sample  # 遗忘因子低通滤波
                    cVQ = min(1.0, cVQ + CINC)  # 辨识成功，置信度计数器增加

            # 辨识sVP：电压对储能有功灵敏度 dV/dPess
            if abs(dV) <= DVMAX and abs(dP) >= DMIN and abs(dP) >= DOM * abs(dQ):
                sample = (dV - sVQ * dQ) / dP   # 剔除无功扰动电压，得到dV/dPess样本
                if SVPMIN <= sample <= SVPMAX:
                    sVP = (1.0 - ALPHA) * sVP + ALPHA * sample
                    cVP = min(1.0, cVP + CINC)

            # 辨识kP：线路‑储能有功耦合系数 dPline / dPess
            if abs(dP) >= DMIN and abs(dP) >= DOM * abs(dQ):
                sample = -dPL / dP
                if KMIN <= sample <= KMAX:
                    kP = (1.0 - KALPHA) * kP + KALPHA * sample
                    cKP = min(1.0, cKP + CINC)

            # 辨识kQ：线路‑储能无功耦合系数 dQline / dQess
            if abs(dQ) >= DMIN and abs(dQ) >= DOM * abs(dP):
                sample = -dQL / dQ
                if KMIN <= sample <= KMAX:
                    kQ = (1.0 - KALPHA) * kQ + KALPHA * sample
                    cKQ = min(1.0, cKQ + CINC)

            # 更新锚点，计数器清零，准备下一个辨识窗口
            anchorV      = Vpu
            anchorPline  = Pline
            anchorQline  = Qline
            anchorPess   = Pess
            anchorQess   = Qess
            estimatorCount = 0.0

    # 辨识参数限幅，防止辨识结果跑飞
    sVP = min(SVPMAX, max(SVPMIN, sVP))
    sVQ = min(SVQMAX, max(SVQMIN, sVQ))
    kP  = min(KMAX, max(KMIN, kP))
    kQ  = min(KMAX, max(KMIN, kQ))

    # VnativeEstimate：剥离储能贡献，估算储能置0时节点固有电压，用于低压退出判断
    VnativeEstimate = Vpu - sVP * Pess - sVQ * Qess

    # -------- 低压状态机 --------
    if low < 0.5:
        # 当前不在低压模式，检测是否进入低压
        if Vpu < VON:
            low = 1.0
            recoverCount = 0.0
    else:
        # 当前处于低压模式，判断退出条件
        if Vpu > VOFF and VnativeEstimate > VON:
            recoverCount = min(RECOVER, recoverCount + 1.0)
        else:
            recoverCount = 0.0

        # 连续满足恢复条件达到RECOVER周期，退出低压模式，复位标志
        if recoverCount >= RECOVER:
            low = 0.0
            recoverCount = 0.0
            uvCount = 0.0
            qLatched = 0.0

    # Pnative/Qnative：剥离储能影响，储能置0时预估的线路功率
    Pnative = Pline + kP * Pess
    Qnative = Qline + kQ * Qess
    Snative = math.hypot(Pnative, Qnative)   # 固有线路视在功率
    Sline   = math.hypot(Pline, Qline)       # 当前实际线路视在功率

    nativeLoading = 0.0
    Slimit = 0.0
    # 计算线路视在功率限制与固有负载率，增加EPS避免除零
    if loading > EPS and Sline > EPS:
        Slimit = LTARGET * Sline / loading        # 换算线路视在功率限制值
        nativeLoading = loading * Snative / Sline # 固有负载率（储能置0时线路负载率）

    # -------- 过载方向状态机 --------
    if direction == 0.0 and nativeLoading > LON:
        # 无过载，检测到过载，设置过载方向
        if Pnative > 0.0:
            direction = 1.0
        elif Pnative < 0.0:
            direction = -1.0
    elif direction != 0.0 and nativeLoading < LOFF:
        # 过载消失，清除过载标记
        direction = 0.0

    # 输出指令初始化
    Pcmd = 0.0
    Qcmd = 0.0
    mode = 0.0

    # -------- 模式1：低压模式，优先无功调压，无功饱和后切有功调压 --------
    if low > 0.5:
        eV = VREF - Vpu  # 电压偏差
        rawQ = Qess + GQ * eV / max(sVQ, SVQMIN)  # 无功调压指令，分母防除0
        Qcmd = min(QMAX, max(0.0, rawQ))

        # 无功饱和延时锁存逻辑
        if qLatched < 0.5:
            if Qcmd >= QREACH * QMAX and Qess >= QREACH * QMAX:
                uvCount = min(DWELL, uvCount + 1.0)
            else:
                uvCount = max(0.0, uvCount - 1.0)
            if uvCount >= DWELL:
                qLatched = 1.0

        # 无功饱和锁存，SOC尚有裕度，投入有功调压
        if qLatched > 0.5 and SOC > SOCMIN + SMARGIN:
            Pvoltage = min(PMAX, max(0.0, Pess + GP * eV / max(sVP, SVPMIN)))
            Pcmd = Pvoltage
            # 同时存在正向过载，叠加过载约束修正有功指令
            if direction > 0.0 and Slimit > EPS:
                qAfter = Qnative - kQ * Qcmd
                pTarget = math.sqrt(max(Slimit*Slimit - qAfter*qAfter, 0.0))
                Pover = max(0.0, (Pnative - pTarget) / max(kP, KMIN))
                Pcmd = max(Pcmd, min(PMAX, Pover))
        mode = 1.0
    # -------- 模式2正向过载 / 模式3反向过载，无低压只处理线路过载 --------
    elif direction != 0.0 and Slimit > EPS:
        pTarget = math.sqrt(max(Slimit*Slimit - Qnative*Qnative, 0.0))
        if direction > 0.0 and SOC > SOCMIN + SMARGIN:
            Pcmd = min(PMAX, max(0.0, (Pnative - pTarget) / max(kP, KMIN)))
            mode = 2.0
        elif direction < 0.0 and SOC < SOCMAX - SMARGIN:
            Pcmd = max(-PMAX, min(0.0, (Pnative + pTarget) / max(kP, KMIN)))
            mode = 3.0
    # -------- mode=0 正常模式，复位锁存相关计数器 --------
    else:
        uvCount = 0.0
        qLatched = 0.0

    # 组装下一时刻完整状态向量stateNext(20维) → 输出给1/z延迟
    stateNext = [
        low, recoverCount, direction, uvCount, qLatched,
        anchorV, anchorPline, anchorQline, anchorPess, anchorQess,
        estimatorCount, sVP, sVQ, kP, kQ,
        cVP, cVQ, cKP, cKQ, initialized
    ]
    sVPout = sVP
    sVQout = sVQ
    kPout = kP
    kQout = kQ
    confidence = 0.25 * (cVP + cVQ + cKP + cKQ)  # 辨识置信度，4个辨识计数器取平均

    return Pcmd, Qcmd, mode, stateNext, sVPout, sVQout, kPout, kQout, confidence

# 控制器参数p数组（直接使用你提供的原始序列）
CONTROLLER_P = [
    0.0050000000000000001,
    0.94999999999999996,
    0.97499999999999998,
    0.97999999999999998,
    1,
    0.94999999999999996,
    0.94999999999999996,
    0.80000000000000004,
    0.40000000000000002,
    0.10000000000000001,
    0.90000000000000002,
    0.02,
    0.94999999999999996,
    10,
    10,
    0.75,
    0.65000000000000002,
    0.12,
    0.10000000000000001,
    0.080000000000000002,
    0.0030000000000000001,
    10,
    1.5,
    0.01,
    0.40000000000000002,
    0.01,
    0.40000000000000002,
    0.029999999999999999,
    1,
    1,
    0.080000000000000002,
    0.20000000000000001,
    1.5,
    0.050000000000000003,
    9.9999999999999995e-07
]

# 控制器初始状态，模拟Simulink 1/z延迟模块输出
controller_state_delayed = [
    0.0,0.0,0.0,0.0,0.0,
    0.0,0.0,0.0,0.0,0.0,
    0.0,0.0,0.0,0.0,0.0,
    0.0,0.0,0.0,0.0,0.0
]
# =====================【嵌入控制器部分 结束】=====================

# =====================【CSV数据记录 开始】=====================
import os
RECORD_CSV = "modbus_realtime_records.csv"
print("CSV完整保存路径：", os.path.abspath(RECORD_CSV))

def record_init():
    """初始化csv，写表头，文件不存在才写表头"""
    import os
    if not os.path.exists(RECORD_CSV):
        with open(RECORD_CSV, "w", newline="", encoding="utf‑8‑sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp","Vpu","Pline","Qline","loading","SOC",
                "Pess","Qess","Pcmd","Qcmd","mode"
            ])

def record_append(timestamp,Vpu,Pline,Qline,loading,SOC,Pess,Qess,Pcmd,Qcmd,mode):
    """追加一行记录"""
    with open(RECORD_CSV, "a", newline="", encoding="utf‑8‑sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp,
            round(Vpu,6),
            round(Pline,6),
            round(Qline,6),
            round(loading,6),
            round(SOC,6),
            round(Pess,6),
            round(Qess,6),
            round(Pcmd,6),
            round(Qcmd,6),
            round(mode,2)
        ])
# =====================【CSV数据记录 结束】=====================


client = None                # Modbus‑TCP客户端全局对象，初始空
is_running = False           # 轮询使能标记：True开启周期读‑算‑写闭环，False停止

def parse_regs(reg_list: list, data_type: str, byte_order: str, swap_word: bool):
    """
    通用解析函数：一组16位寄存器 → 解析成对应数值
    :param reg_list: 输入寄存器列表，长度由data_type决定
    :param data_type: "int16"/"int32"/"float32"/"double64"
    :param byte_order: "big"大端 / "little"小端
    :param swap_word: True 交换字顺序(反转reg_list列表)，False不交换
    :return: (解析后数值, 原始打包字节的十六进制字符串)
    """
    if swap_word:                     # 如果开启交换字，把寄存器列表整体反转
        reg_list = list(reversed(reg_list))
    if byte_order == "big":           # 判断字节序，设置struct端标识
        endian_char = ">"
    else:
        endian_char = "<"
    # 根据数据类型选择struct格式化符号
    if data_type == "int16":
        fmt = endian_char + "h"
    elif data_type == "int32":
        fmt = endian_char + "i"
    elif data_type == "float32":
        fmt = endian_char + "f"
    elif data_type == "double64":
        fmt = endian_char + "d"
    else:
        return None, ""
    # 把16位无符号寄存器打包成原始字节流
    raw_bytes = struct.pack(">" + "H"*len(reg_list), *reg_list)
    hex_str = raw_bytes.hex()         # 转为十六进制字符串用于日志打印
    val = struct.unpack(fmt, raw_bytes)[0] # 按照目标格式解包得到物理数值
    return val, hex_str

def value_to_regs(value, data_type: str, byte_order: str, swap_word: bool):
    """
    通用转换：输入数值 → 转换成modbus 16位寄存器列表，用于写寄存器下发
    :param value: int/float待转换数值
    :param data_type: "int16"/"int32"/"float32"/"double64"
    :param byte_order: "big"/"little"
    :param swap_word: 是否反转寄存器字列表
    :return: (寄存器列表, 字节十六进制字符串)
    """
    if byte_order == "big":
        endian_char = ">"
    else:
        endian_char = "<"
    # 选择打包格式
    if data_type == "int16":
        fmt = endian_char + "h"
    elif data_type == "int32":
        fmt = endian_char + "i"
    elif data_type == "float32":
        fmt = endian_char + "f"
    elif data_type == "double64":
        fmt = endian_char + "d"
    else:
        return [], ""
    raw_bytes = struct.pack(fmt, value) # 将物理数值按照字节序打包为二进制字节
    hex_str = raw_bytes.hex()
    reg_count = len(raw_bytes) // 2     # 每2字节 =1个modbus 16位寄存器，计算寄存器个数
    regs = list(struct.unpack(">" + "H"*reg_count, raw_bytes)) # 拆分成16位寄存器
    if swap_word:                       # 如果需要交换字，反转寄存器列表
        regs = list(reversed(regs))
    return regs, hex_str

def msg_append(txt: str):
    """操作信息文本框追加日志，自动滚动到底部"""
    msg_text.insert(tk.END, txt + "\n")
    msg_text.see(tk.END)

def connect_modbus():
    """【连接按钮回调函数】点击连接，建立modbus tcp连接，启动周期轮询poll_loop"""
    global client, is_running    # 使用全局的client、is_running变量
    if client and client.is_socket_open(): # 判断已经连接，直接返回，避免重复连接
        log_text.insert(tk.END, "已经连接\n")
        log_text.see(tk.END)
        return
    try:
        dev_ip = entry_ip.get().strip()       # 获取UI输入框IP
        dev_port = int(entry_port.get().strip()) # 获取端口转为int
        slave_id = int(entry_slave.get().strip()) # 获取从站ID
    except ValueError:                         # 输入不是数字，捕获异常
        log_text.insert(tk.END, "❌参数输入错误，请检查数字\n")
        log_text.see(tk.END)
        return
    client = ModbusTcpClient(host=dev_ip, port=dev_port, timeout=2) # 创建tcp客户端对象，超时2秒
    ok = client.connect() # 发起TCP连接
    if ok:
        log_text.insert(tk.END, f"✅连接成功 {dev_ip}:{dev_port} 从站{slave_id}\n")
        msg_append(f"[系统]建立TCP连接，读取保持寄存器0‑27，启动闭环控制")
        is_running = True  # 置标志位，开启轮询
        poll_loop()        # 启动第一次周期轮询
    else:
        log_text.insert(tk.END, "❌连接失败，请确认modbus服务已开启\n")
    log_text.see(tk.END)

def disconnect_modbus():
    """【断开按钮回调函数】断开TCP连接，停止轮询"""
    global client, is_running
    is_running = False                 # 轮询标志置False，终止下一轮poll_loop调度
    if client:
        client.close()                 # 关闭modbus tcp socket
        log_text.insert(tk.END, "🔌已断开连接\n")
        msg_append("[系统]TCP连接已关闭，停止控制器")
    log_text.see(tk.END)

def poll_loop():
    """
    周期闭环轮询函数，每1秒执行一次(root.after(1000,...))
    1.读保持寄存器0‑27（28个）得到7组现场测量量
    2.调用control控制器计算Pcmd Qcmd
    3.把Pcmd写入28‑31，Qcmd写入32‑35
    4.更新控制器状态stateNext.copy()，模拟Simulink 1/z延迟
    """
    # 必须声明全局，修改controller_state_delayed才会修改全局对象，而不是生成局部变量
    global client, is_running, controller_state_delayed
    # 判断：未运行 / client为空 / socket没有打开，直接退出本轮循环
    if not is_running or client is None or not client.is_socket_open():
        return
    try:
        slave_id = int(entry_slave.get().strip()) # 获取UI从站ID
        start_addr = 0                            # 读取寄存器起始地址0
        reg_cnt = 28                              # 一共读28个寄存器(0‑27)
        # 获取界面下拉框的配置（仅用于UI调试显示，闭环算法不使用这组配置）
        sel_data_type = comb_data_type.get()
        sel_byte_order = comb_byte_order.get()
        sel_swap_word = (comb_swap_word.get() == "交换字顺序(反转寄存器列表)")
        # UI下拉框文本映射为程序内部标识符
        dt_map = {"16位Int":"int16","32位Int":"int32","32位Float":"float32","64位Double":"double64"}
        bo_map = {"大端(高字节在前)":"big","小端(低字节在前)":"little"}
        data_type = dt_map[sel_data_type]
        byte_order = bo_map[sel_byte_order]
        reg_len_map = {"int16":1,"int32":2,"float32":2,"double64":4}
        step = reg_len_map[data_type]

        # modbus功能码03读取保持寄存器；pymodbus>=3.10参数device_id
        resp = client.read_holding_registers(address=start_addr, count=reg_cnt, device_id=slave_id)
        if resp.isError(): # 判断modbus响应是否报错
            log_text.insert(tk.END, f"通讯异常:{resp}\n")
            msg_append(f"[读寄存器]异常:{resp}")
        else:
            msg_append(f"[读寄存器]功能码03，起始{start_addr},数量{reg_cnt}")
            # 清空GUI表格treeview全部旧行
            for row in tree.get_children():
                tree.delete(row)
            # 把读到的原始16位寄存器填入UI表格
            for idx, val in enumerate(resp.registers):
                addr = start_addr + idx
                tree.insert("", tk.END, values=(addr, val))

            log_text.insert(tk.END,f"=====解析配置：数据类型:{sel_data_type}｜字节序:{sel_byte_order}｜交换字:{sel_swap_word} =====\n")
            offset = 0
            group_idx = 0
            # 根据UI下拉配置循环解析，打印日志（仅调试看数据，闭环算法不用）
            while offset + step <= reg_cnt:
                reg_slice = resp.registers[offset:offset+step]
                val, hex8 = parse_regs(reg_slice, data_type, byte_order, sel_swap_word)
                reg_range = f"{offset}~{offset+step-1}"
                log_text.insert(tk.END,f"【组{group_idx}】寄存器{reg_range}｜原始:{reg_slice}｜hex:{hex8}｜解析值={val:.8g}\n")
                offset += step
                group_idx +=1

            # =========闭环控制：硬编码解析7个测量量，不受UI下拉框干扰========
            Vpu, _     = parse_regs(resp.registers[0:4], "double64", "big", False)
            Pline, _   = parse_regs(resp.registers[4:8], "double64", "big", False)
            Qline, _   = parse_regs(resp.registers[8:12], "double64", "big", False)
            loading, _ = parse_regs(resp.registers[12:16], "double64", "big", False)
            SOC, _     = parse_regs(resp.registers[16:20], "double64", "big", False)
            Pess, _    = parse_regs(resp.registers[20:24], "double64", "big", False)
            Qess, _    = parse_regs(resp.registers[24:28], "double64", "big", False)

            # 调用移植自Simulink的自适应控制器control函数
            Pcmd, Qcmd, mode, stateNext, sVPout, sVQout, kPout, kQout, confidence = control(
                Vpu, Pline, Qline, loading, SOC, Pess, Qess,
                controller_state_delayed, CONTROLLER_P
            )
            # .copy()复制列表对象，防止stateNext与全局变量引用同一个列表对象，状态错乱
            controller_state_delayed = stateNext.copy()

            # 打印控制器输出信息到操作日志框
            msg_append(f"[控制器输出] mode={mode} Pcmd={Pcmd:.4f} Qcmd={Qcmd:.4f} conf={confidence:.3f}")

            # --------保存到csv记录--------
            record_init()
            now_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            record_append(now_str,Vpu,Pline,Qline,loading,SOC,Pess,Qess,Pcmd,Qcmd,mode)

            # 将控制器输出Pcmd、Qcmd转为寄存器列表，准备下发子站
            regs_pcmd, _ = value_to_regs(Pcmd, "double64", "big", False)
            # 批量写保持寄存器功能码16，写Pcmd到28‑31，pymodbus>=3.10 API
            resp_w1 = client.write_registers(address=28, values=regs_pcmd, device_id=slave_id)

            regs_qcmd, _ = value_to_regs(Qcmd, "double64", "big", False)
            # Qcmd写入32‑35
            resp_w2 = client.write_registers(address=32, values=regs_qcmd, device_id=slave_id)

            if resp_w1.isError() or resp_w2.isError():
                msg_append("[写指令] ❌下发Pcmd/Qcmd失败(写28‑35)")
            else:
                msg_append(f"[写指令] ✅下发 Pcmd({Pcmd:.4f})→28‑31, Qcmd({Qcmd:.4f})→32‑35")
    except Exception as e: # 捕获全部异常，打印到日志，防止GUI直接崩溃退出
        log_text.insert(tk.END, f"读取/控制异常:{str(e)}\n")
        msg_append(f"[异常] {str(e)}")
    log_text.see(tk.END)
    # tkinter定时器，20ms之后再次调用poll_loop，实现周期轮询闭环
    root.after(20, poll_loop)

def btn_write_general():
    """【手动写入按钮回调】UI手动输入数值，转换后写modbus寄存器，用于调试"""
    global client
    if client is None or not client.is_socket_open():
        log_text.insert(tk.END, "❌未建立连接，无法写入\n")
        log_text.see(tk.END)
        return
    try:
        slave_id = int(entry_slave.get().strip())
        addr = int(entry_w_addr.get())          # 获取输入框目标寄存器起始地址
        input_str = entry_w_val.get().strip()   # 获取待写入的数字字符串
        # 获取下拉框配置
        sel_data_type = comb_data_type.get()
        sel_byte_order = comb_byte_order.get()
        sel_swap_word = (comb_swap_word.get() == "交换字顺序(反转寄存器列表)")
        dt_map = {"16位Int":"int16","32位Int":"int32","32位Float":"float32","64位Double":"double64"}
        bo_map = {"大端(高字节在前)":"big","小端(低字节在前)":"little"}
        data_type = dt_map[sel_data_type]
        byte_order = bo_map[sel_byte_order]
        # 根据类型转为int或者float
        if data_type in ("int16","int32"):
            val = int(input_str)
        else:
            val = float(input_str)
        regs, hex_str = value_to_regs(val, data_type, byte_order, sel_swap_word)
        msg_append(f"[写数据]类型:{sel_data_type},地址{addr},值={val},寄存器列表:{regs},hex:{hex_str}")
        # 判断寄存器数量，1个用功能码06；多个寄存器使用功能码16批量写
        if len(regs) == 1:
            resp = client.write_register(address=addr, value=regs[0], device_id=slave_id)
        else:
            resp = client.write_registers(address=addr, values=regs, device_id=slave_id)
        if resp.isError():
            log_text.insert(tk.END, f"❌写入失败，地址{addr}\n")
        else:
            log_text.insert(tk.END, f"✅写入成功，地址{addr}，写入值={val}\n")
    except Exception as e:
        log_text.insert(tk.END, f"写入异常:{str(e)}\n")
    log_text.see(tk.END)

def on_closing():
    """窗口关闭事件回调：关闭窗口前先断开modbus连接，释放资源"""
    disconnect_modbus()
    root.destroy()

# =================GUI界面布局=================
root = tk.Tk()                              # 创建tk主窗口对象
root.title("Modbus‑TCP 储能自适应控制器闭环工具｜读0‑27，写指令28‑35") # 设置窗口标题
root.geometry("1100x860")                   # 设置窗口大小宽×像素高

# =========连接参数区 标签框架容器==========
frame_conn = ttk.LabelFrame(root, text="连接参数配置（读取保持寄存器0‑27共28个）")
frame_conn.pack(pady=6, padx=8, fill=tk.X)  # pack布局，外边距，横向填满窗口
ttk.Label(frame_conn, text="IP地址:").grid(row=0, column=0, padx=4, pady=4) # 静态文字标签，网格布局
entry_ip = ttk.Entry(frame_conn, width=16)   # IP输入框
entry_ip.insert(0, "127.0.0.1")             # 默认填入本地回环地址
entry_ip.grid(row=0, column=1, padx=4, pady=4)
ttk.Label(frame_conn, text="端口:").grid(row=0, column=2, padx=4, pady=4)
entry_port = ttk.Entry(frame_conn, width=8)
entry_port.insert(0, "502")                 # modbus‑tcp默认端口502
entry_port.grid(row=0, column=3, padx=4, pady=4)
ttk.Label(frame_conn, text="从站ID:").grid(row=0, column=4, padx=4, pady=4)
entry_slave = ttk.Entry(frame_conn, width=8)
entry_slave.insert(0, "1")                  # 默认从站ID=1
entry_slave.grid(row=0, column=5, padx=4, pady=4)

# =========解析配置下拉框区域==========
frame_cfg = ttk.LabelFrame(root, text="解析配置（调试用；闭环测量解析硬编码64‑Double大端）")
frame_cfg.pack(pady=4, padx=8, fill=tk.X)
ttk.Label(frame_cfg, text="数据类型:").grid(row=0, column=0, padx=4, pady=4)
comb_data_type = ttk.Combobox(frame_cfg, width=14, state="readonly") #只读下拉选择框
comb_data_type["values"] = ["16位Int","32位Int","32位Float","64位Double"]
comb_data_type.current(3)   # 默认选中索引3，即64位Double
comb_data_type.grid(row=0, column=1, padx=4, pady=4)
ttk.Label(frame_cfg, text="字节序:").grid(row=0, column=2, padx=4, pady=4)
comb_byte_order = ttk.Combobox(frame_cfg, width=18, state="readonly")
comb_byte_order["values"] = ["大端(高字节在前)","小端(低字节在前)"]
comb_byte_order.current(0)  # 默认大端
comb_byte_order.grid(row=0, column=3, padx=4, pady=4)
ttk.Label(frame_cfg, text="字交换:").grid(row=0, column=4, padx=4, pady=4)
comb_swap_word = ttk.Combobox(frame_cfg, width=26, state="readonly")
comb_swap_word["values"] = ["不交换字","交换字顺序(反转寄存器列表)"]
comb_swap_word.current(0)
comb_swap_word.grid(row=0, column=5, padx=4, pady=4)

# 连接断开按钮容器
frame_btn = ttk.Frame(root)
frame_btn.pack(pady=4)
btn_conn = ttk.Button(frame_btn, text="连接", command=connect_modbus) #绑定点击回调connect_modbus
btn_conn.grid(row=0, column=0, padx=12)
btn_dis = ttk.Button(frame_btn, text="断开", command=disconnect_modbus)
btn_dis.grid(row=0, column=1, padx=12)

# =========写入操作区==========
frame_write = ttk.LabelFrame(root, text="写入操作（手动调试，跟随上方配置）")
frame_write.pack(pady=6, padx=8, fill=tk.X)
ttk.Label(frame_write, text="目标起始地址：").grid(row=0, column=0, padx=4, pady=4)
entry_w_addr = ttk.Entry(frame_write, width=12)
entry_w_addr.grid(row=0, column=1, padx=4, pady=4)
ttk.Label(frame_write, text="待写入数值：").grid(row=0, column=2, padx=4, pady=4)
entry_w_val = ttk.Entry(frame_write, width=30)
entry_w_val.grid(row=0, column=3, padx=4, pady=4)
btn_write = ttk.Button(frame_write, text="执行写入", command=btn_write_general)
btn_write.grid(row=0, column=4, padx=10, pady=4)

# =========原始寄存器表格 Treeview==========
tree_frame = ttk.Frame(root)
tree_frame.pack(fill=tk.BOTH, expand=False, padx=8, pady=2)
columns = ("addr", "reg_value")                          # 表格列标识符
tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10) #表格控件
tree.heading("addr", text="寄存器地址")                  # 设置列表头文字
tree.heading("reg_value", text="原始16位寄存器值")
tree.column("addr", width=140, anchor=tk.CENTER)         # 设置列宽度、居中对齐
tree.column("reg_value", width=320, anchor=tk.CENTER)
vsb_tree = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview) #垂直滚动条
tree.configure(yscrollcommand=vsb_tree.set)              # 表格与滚动条绑定
tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
vsb_tree.pack(side=tk.RIGHT, fill=tk.Y)

# =========Modbus操作信息文本框（打印控制器输出、读写事件）==========
msg_label = ttk.Label(root, text="=== Modbus操作信息 / 控制器输出 ===")
msg_label.pack(pady=(4, 0))
msg_frame = ttk.Frame(root)
msg_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=2)
msg_text = tk.Text(msg_frame, height=9, font=("Consolas",9)) #多行文本框，等宽字体方便看日志
msg_vsb = ttk.Scrollbar(msg_frame, orient="vertical", command=msg_text.yview)
msg_text.configure(yscrollcommand=msg_vsb.set)
msg_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
msg_vsb.pack(side=tk.RIGHT, fill=tk.Y)

# =========运行日志（解析结果输出）==========
log_label = ttk.Label(root, text="===运行日志｜原始寄存器组｜十六进制｜解析结果===")
log_label.pack(pady=(4,0))
log_text = tk.Text(root, height=10, font=("Consolas",9))
log_text.pack(fill=tk.BOTH, expand=False, padx=8, pady=4)

root.protocol("WM_DELETE_WINDOW", on_closing) # 绑定窗口关闭事件回调函数
root.mainloop()                                # tkinter主消息循环，启动GUI程序
