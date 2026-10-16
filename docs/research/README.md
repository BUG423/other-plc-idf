# docs/research — 协议逆向研究区（P3 阶段启用）

> **状态：框架已建，内容未启用。** 本区在路线图 **P3「协议抓包评估」**（见
> [../wsl-feasibility/00-synthesis-and-roadmap.md](../wsl-feasibility/00-synthesis-and-roadmap.md)）
> 正式启动前保持空置；届时 AutoShop 私有协议（编译产物下载 / 上载 / 在线监控）与
> USB 编程口 bulk 端点协议的逆向工作全部落在这里。

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
