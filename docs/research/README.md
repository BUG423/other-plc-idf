# docs/research — 协议与固件研究

> **2026-10-19供电后更新**：20号记录24 V台架的固定变量读回、灯程序真实编译/下载/比较、状态与时钟变化，以及用户确认Y0闪烁、STOP后熄灭。18号更正仍适用：旧ff010000/4B不是CPU状态。完整Linux原生包装、无GUI下装及设备字节绑定仍开放；历史报告保留其当时状态。

- [Linux既有全局绑定与真实本地闭环](firmware/21-linux-existing-global-bindings-2026-10-19.md)：8项字段后处理、全符号对照及当前原生编译/监控缺口。
- [接入24 V后的Y0真机台架](firmware/20-powered-led-bench-2026-10-19.md)：实际运行、人工观察、历史失败与编译产物漂移。
- [固定已有变量的ReadMemoryEx](firmware/19-existing-variable-read-2026-10-19.md)：静态来源、22字节读请求、固定项和响应候选；本机后续实测见20号。

- [最新交付、调用链更正与GCM实测](../management/EASY521_AI_FOLLOWUP_2026-10-19.md)：已有变量解析、人工反馈工具及当前验收边界。
- [运行模式/CPU状态更正](firmware/18-runtime-mode-correction-2026-10-19.md)：CRTU/EtherCAT/GCM三条构造与执行路径，修正12/15静态错误。
- [已有变量分配解析](firmware/17-existing-variable-bindings-2026-10-19.md)：GVT19项地址/类型、8项候选符号独立对照，非新分配器。
- [首批真实交付与命令](../management/EASY521_AI_DELIVERY_2026-10-19.md)：独立USB查询、Linux链接、人工反馈合同、单main分支与验证。
- [USB线帧静态合同](firmware/15-transport-contract-2026-10-19.md)：通用帧、序列/响应、端点/配置与离线codec；CRTU→ff010000误接由18号更正，原始帧事实保留。
- [Linux两级链接与应用事务](firmware/16-application-transaction-2026-10-19.md)：真实节字节对照、8项变量符号后处理、有序文件表与配置头。

- [Easy521 最简单直接的控制方式](../management/EASY521_DIRECT_CONTROL_2026-10-18.md)：Modbus 数据控制、CPU 模式、应用下载与系统固件四层分开；附本轮物理网口/USB 枚举和可运行 Linux D 区工具。
- [官方数据协议的准确范围](firmware/13-documented-control-routes-2026-10-18.md)：Modbus/FINS/OPC UA 地址、变量发布、CPU STOP 语义与归档变量实际绑定。
- [无 GUI 通信实现与本地变量服务](firmware/12-interface-control-2026-10-18.md)：IH530 工厂/状态查询链及匹配 PDB 的包头、枚举和数据提供关系缺口。
- [完整应用文件与下载调用点](firmware/14-easy521-download-paths-2026-10-18.md)：真实18文件成功项、31个调用点及独立本地仿真器协议入口。
- [普通 ST 到 ARM 对象的真实实验](firmware/11-plain-main-object-2026-10-18.md)：文本预处理、隐式声明、正负实验与未完成的完整应用链接。

- [平台路线与固件可行性复核](firmware/03-feasibility-assessment-2026-10-17.md)：四条路线、事实/推断/TBD、历史调查勘误及研究门槛。
- [ARM ELF 离线证据](firmware/20261017-offline-elf-inspection.json)：工具版本、目标属性、原始输出、文件哈希。
- [官方固件与芯片来源复核](firmware/04-official-sources-2026-10-17.md)：四核官方表述、可获取固件元数据与缺失的准确 SoC/BSP；未取得 Easy521 系统镜像。
- [本机工具链拆解](firmware/05-local-toolchain-2026-10-17.md)：Windows ST 前端、ARM ELF 库、Linux 烧录库存在性线索与实际缺口；未发布厂商原文件。
- [Linux 链接实验](firmware/06-linux-link-experiment-2026-10-17.md)：用原生 Linux LLD 对已有厂商 ARM 对象做可重定位链接，保留失败对照；不是 ST 编译或真机部署验收。
- [原生多目标架构决定](../architecture/ADR-0002-native-multitarget.md)：两条路线、目标接口、六项有界任务与逐层验收。
- [厂商应用分析工具与差分](firmware/07-vendor-application-inspection-2026-10-17.md)：真实 12 文件、6 组对照，ELF 与不透明容器分级。
- [镜像检查器与硬件证据矩阵](firmware/08-hardware-evidence-and-image-inspection-2026-10-17.md)：通用 ELF/uImage/DTB 格式检查，不将平台字符串当作芯片认定。
- `firmware/01-manual-facts.md`、`02-sunxi-ecosystem.md`：2026-10-16 历史资料，保留原文；结论采用最新复核的限定。
- [实施计划](../management/PLAN.md) 中 R01/R02：下一批研究工作，独立于主线桥接交付。

## 目录规划（启用时创建）

```
docs/research/
├── captures/          # 抓包记录：原始 pcap / USBPcap 文件 + 每次抓包一份 meta
│                      #   meta 内容：日期、工具与版本（如 USBPcap）、抓的是哪条通道、
│                      #   触发操作（连接/下载/监控）、时长、文件大小、TUN 是否已绕开
├── frame-analysis/    # 帧结构分析：对 captures 的解读文档
│                      #   帧格式、字段表（偏移/长度/含义/取值）、会话状态机、校验方式
├── findings.md        # 发现日志：按时间追加的结论条目（只增不改，修正用新条目）
└── decisions/         # 决定记录（ADR）：ADR-NNNN-短标题.md，从 0001 起编号
```

## ADR（决定记录）模板

```markdown
# ADR-NNNN: <决定标题>

- 日期：YYYY-MM-DD
- 状态：proposed | accepted | superseded by ADR-MMMM

## 背景
<当时面对的事实与约束；引用 captures/ 或 frame-analysis/ 中的证据路径>

## 决定
<一句话说清决定了什么>

## 依据
<支撑该决定的证据与分析，逐条给出可核查的出处>

## 后果
<正面 / 负面 / 需要后续跟进的事项>
```

## 使用说明（启用时生效）

1. **证据先行**：findings.md 与 ADR 里的每条结论都必须链接到 `captures/` 或
   `frame-analysis/` 下的具体文件；没有原始数据支撑的猜测只能标 `假设`，不得写成结论。
2. **原始数据只增不改**：抓包文件导入后视作只读证据，分析中发现问题就重新抓一次，
   不修改旧文件。
3. **抓包红线**：抓包对象是 AutoShop 与真机之间的会话，需用户明确授权且在场；
   Windows 侧装 USBPcap 需管理员权限（一次）；抓包前确认代理 TUN 已绕开，否则数据作废
   （见 `wsl-probe/plc_discover_run2_INVALID_tun_fakeaccept.json` 的教训）。
4. **对真机只读边界不变**：逆向实验中向 PLC 发送探测帧属于写通道行为，必须逐次
   获得授权并记录在 findings.md；被动的抓包解析不接触真机。
5. **产出流向**：帧结构稳定后的协议文档转入 `docs/architecture/`（原生后端设计引用之），
   实现代码落在 `src/plc_idf/`（v0.2 更名前为 `src/py2plc/`）；本区只放证据与分析过程。

## P3 启用清单

- [ ] 用户确认立项 P3 抓包评估
- [ ] Windows 侧安装 USBPcap（管理员，一次）
- [ ] 首次抓包：AutoShop 正常连接会话（授权 + 在场 + TUN 绕开）
- [ ] 建 `captures/`、`frame-analysis/`、`decisions/` 目录并导入首份 meta
- [ ] 帧结构初步分析写入 `frame-analysis/`，里程碑结论记入 `findings.md`
- [ ] 「是否立项原生 USB 协议栈」以 ADR-0001 形式定案
