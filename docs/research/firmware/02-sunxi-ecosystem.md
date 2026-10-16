# Easy521 底层生态调研：全志 sunxi 平台判定、FEL/调试口知识、固件安全机制与风险边界

- 日期：2026-10-16。方法：纯 Web 调研（本子任务不接触设备）。实测线索承自项目记录：USB 枚举字符串 `"Easy52x USB Device v2.4"` / manufacturer `"Linux 3.10.65 with sunxi_usb_udc"`，编程口为 CDC 拓扑（bulk EP 0x02/0x81）。
- 可信度标注：**【官方】**=厂商一手资料；**【社区·高】**=linux-sunxi wiki/U-Boot 官方文档/sunxi-tools 源码仓库等有多年实测背书；**【社区·中】**=论坛实拆帖/编译教程/有实物图的博客；**【传言】**=内容农场/营销页/无法核实的说法。
- 本笔记与 `01-manual-facts.md`（手册侧事实）互补：01 讲"手册公开了什么"，本文讲"开源世界对这个平台知道什么"。

---

## ① SoC 判定与证据链

### 1.1 "Linux 3.10.65 + sunxi_usb_udc" 是全志 3.10 BSP 家族的签名

- `sunxi_usb_udc` 不是主线内核驱动，只存在于全志 BSP（lichee）内核树 `drivers/usb/sunxi_usb/` 下，manufacturer 字符串 `Linux <版本> with sunxi_usb_udc` 由该 BSP 的 USB gadget 框架自动生成【社区·高：linux-sunxi wiki《Linux mainlining effort》对该 BSP 驱动"非通用、BSP 私有包装"的记载 https://linux-sunxi.org/Linux_mainlining_effort 】。
- 内核版本 **3.10.65 精确对应全志官方 BSP**。公开可复现的编译记录：F1C100s 官方 BSP 编译输出 `Image Name: Linux-3.10.65`（whycan 社区两篇 step-by-step 编译教程，含完整构建日志：https://whycan.com/t_1014.html 、https://whycan.com/t_1522.html ）【社区·中】。
- 同一 3.10.65 BSP 代还覆盖几个 SoC 系（按公开资料）：**suniv（F1C100s/F1C200s）**、**sun8iw11（A40i/T3/T3-B 工业系）**、**sun50iw1（A64，平板系）**【社区·高：linux-sunxi SoC 家族表 https://linux-sunxi.org/Allwinner_SoC_Family ；Forlinx T3 板卡页明示 "System: Linux3.10" https://www.forlinx.net/product/t3-95.html ；Geekbench 实机记录 "ARM sun8iw11 @ 1.20 GHz" https://browser.geekbench.com/v4/cpu/baseline/15625520 】。
- 结论：manufacturer 字符串把范围锁死在"全志 + 3.10 BSP 代"，但**不能单靠内核版本区分 SoC 型号**。

### 1.2 候选 SoC 逐一对照 Easy521-0808TN 硬件画像

Easy521 的硬件画像（承 01 笔记）：2×10/100M RJ45（EtherNET1/2）、CN3 EtherCAT 主站（最多 72 从站）、Type-C USB 设备口、RS485、可选 GE20-TF 卡、128K 步程序、固件 6.11.0.1。

| 候选 | 核心 | BSP 内核 | 与画像吻合度 | 关键反证 |
|---|---|---|---|---|
| **F1C100s/F1C200s（suniv）** | ARM926EJ-S @533MHz，内置 32/64MB DDR | 3.10.65（实锤，见上） | USB OTG/gadget 完备；工业小设备先例多（Hantek DSO2X1X 示波器即 F1C200s） | **无任何原生以太网 MAC**（linux-sunxi F1C100s 页外设清单仅 SD/MMC+USB OTG+LCD/CVBS，https://linux-sunxi.org/F1C100s ），双网口需外挂 2 片外部 MAC；533MHz 单核 ARM9 跑 72 从站 EtherCAT 主站+逻辑引擎，算力存疑 |
| **A40i/T3（sun8iw11）** | 4×Cortex-A7 @1.2GHz，工业级（-40~+85℃ 宣传） | 3.10 BSP（Forlinx OKT3-C 明示 Linux 3.10/Android 7.1） | **原生双 MAC：10/100 EMAC（MII）+1G GMAC（RGMII）**，双网口天然直配（3D Photonics A40i 模组页 https://www.3dphotonix.com/som/a40i ；CNX-Software A40i 工业级介绍 https://www.cnx-software.com/2018/05/14/… ）；官方宣传"军工级/工业级"处理器与 PLC 定位一致 | 成本高于 F1 系；公开资料中尚无"A40i 用于 PLC"的直接报道 |
| A64（sun50iw1） | 4×A53（平板芯） | 3.10.65 | — | 平板定位、单 GMAC、无工业轨迹，基本排除 |

- 旁证（架构形态）：汇川官方排障文档写明小型 PLC 固件更新要**先刷"PLC 固件升级包"、再刷"FPGA 软件"，且两包版本必须匹配**（gongkong《汇川技术小型PLC&HMI常见故障处理》168 页 PDF，http://sc19.gongkong.com/Upload/gongkong/technicalDataAttachment/201807/10/28fea753563c4ffcb4f96b699033da0b.pdf ）【官方/半官方渠道文档】——即整机是 **"SoC（跑 Linux）+ FPGA（跑 IO/现场总线）"** 双芯片架构，与第三方拆解一致（见 ②）。这也解释了为何 SoC 只需承担通信/逻辑调度：对 SoC 算力要求比"纯软件 PLC"低，F1C200s 的可能性不能完全排除。
- 官方营销页称 Easy 系列用"高性能双核处理器"（汇川官网 Easy 系列卧式产品页 https://www.inovance.com/product/details?productId=740 ）【官方，营销语言】——"双核"很可能指"处理器+FPGA"或为双网口形态机型的说法，**不足以作为 SoC 核数证据**，全志 3.10.65 BSP 家族内没有明确的双核型号与之对齐。
- **判定结论（按可能性排序）**：
  1. **A40i/T3（sun8iw11）** —— 双原生 MAC 与工业定位最贴合，当前最优候选【推断，置信度中高】；
  2. **F1C200s（suniv）** —— 若成本极致压缩、以太网用外部芯片方案则可能，且其 BSP 教程与 3.10.65 的关联在公开资料中最"出名"【推断，置信度中低】；
  3. 其他 3.10 BSP 型号（A64 等）—— 排除或极低。
- **最终判定必须线下**：开盖看主控丝印，或拿到 FEL `ver` 输出/`/proc/cpuinfo`（见 ⑥）。两条路都不需要"碰网上运行的设备"。

---

## ② 公开固件/拆解资料清单

### 2.1 固件获取渠道（官方，可直接下载）

| 资源 | 内容 | 来源 | 可信度 |
|---|---|---|---|
| **InoPlanet 开发者平台·H5U&Easy 固件页** | "AutoShop 配套的 H5U&Easy 系列 PLC 固件，用于版本匹配、固件更新及部分已知异常恢复"；**固件升级包格式 `.upgrade`、固件烧录包格式 `.img`；更新通道：以太网 / USB / SD 卡**；免费下载（可能需注册） | https://www.inoplanet.com/developerResources/details/firmware-001-002 （汇川官方开发者平台"汇川技术星球"） | **官方** |
| InoPlanet 资源主页 | 还有 AM300/500/760 & AC700/800 系列 PLC 固件，按型号+版本选择 | https://inoplanet.com/developerResources | **官方** |
| 官网下载中心 | AutoShop V4.10.1.1、H5U/Easy 用户手册 | https://www.inovance.com/allResult?key=H5U | **官方** |

- **这直接推翻"固件文件需向汇川技术人员获取"的旧认知**（01 笔记引编程手册 p961 原话）。手册写那句话的时代信息已过时：固件包现在官方公开发放。这是本调研最有价值的新事实之一。
- 与 01 笔记手册侧事实对齐良好：手册说"固件烧录文件按机型族分两个（Easy30X/32X/50X 一个、Easy52X 一个）"——`.img` 命名与 PhoenixCard 制卡流程吻合（PhoenixCard 即全志官方 SD 量产卡制作工具，把 `.img` 做成可自动烧写的量产卡：Pine64 官方文档 https://docs.pine64.org ；Tina Linux 社区文档 https://docs.aw-ol.com ）【社区·高】。
- 注意：**拿到 `.img` 不等于能安全刷**。多机型族共包 + PLC 包/FPGA 包版本配对约束 + 验证码机制（见 ④）都意味着"刷错"场景丰富，见 ⑤。

### 2.2 拆解/硬件资料（全部为间接证据，无一确认全志主控）

| 资料 | 内容 | 来源 | 可信度 |
|---|---|---|---|
| 知乎《汇川技术PLC拆解：国产FPGA实战》(2025-09) | 某汇川 PLC：主控板+IO 接口板+电源板三 PCB 堆叠，主要器件涂覆保护涂层，亮点为国产 FPGA 上机 | https://zhuanlan.zhihu.com/p/1948404513393337278 | **社区·中**（有图，但未点出 SoC 丝印；且不能确认拆的是 Easy 系） |
| 数码之家 H5U 拆解帖 | 号称论坛首拆 H5U，有内部照片 | https://www.mydigit.cn/thread-278492-1-1.html | **社区·中**（图片内容本次未能核读，待线下比对） |
| 工控网 EASY 系列拆机讨论 (2023-04) | Easy 系无电池（仅时钟电池/超级电容之说）、Type-C 编程口、双网口设计 | gkong.com 论坛帖（本次仅获摘要，原文未能抓取） | **社区·中** |
| CSDN 对 H3S 的分析 | 汇川小型 PLC 为 "ARM+FPGA" 双芯架构：ARM 跑逻辑、FPGA 跑现场总线与高速 IO | CSDN 博客（检索摘要） | **社区·中** |
| 淘宝拆机件话题 | 称 H5U-1614MTD 主控为"瑞萨 RA4M1 MCU 120MHz"等 | https://shuma.taobao.com/topic/... （检索摘要） | **传言**——RA4M1 是 Cortex-M4 级 MCU，不可能承载 2 网口+EtherCAT+Linux，几乎可断定是营销文案张冠李戴，**不要采信** |
| 日文 note.com《INOVANCE PLC Easy Series 调查》 | 号称"看看 Easy 内部"的拆查文章 | https://note.com/kzr8302/n/nb9e753bad070 | **社区·中**（本次抓取失败，待补） |

### 2.3 系统级逆向/协议逆向

- **全网零命中**：GitHub（topics/仓库）、CSDN、看雪系、Gitee 均未发现针对 H5U/Easy 的固件解包、编程口协议逆向、AutoShop 工程解密等公开项目；仅有通用 ICS 逆向框架（如 CODESYS 二进制逆向框架 ICSREF https://github.com/momalab/ICSREF ，不适用于 AutoShop 私有运行时）【社区·高：确认"无"本身也是一种结果】。
- 与项目实测一致：AutoShop 编程协议全网零逆向。**该平台在开源社区是空白地带**——既说明没有现成轮子，也说明一旦做出结果就是独一份。
- CVE/CNVD/CISA 侧：**未检索到任何针对汇川 PLC 的公开漏洞通告**（CISA ICS、CNVD 周报、第三方聚合页均无）；汇川设有官方 PSIRT 渠道（https://www.inovance.com/portal/serviceSupport/psirt ）【官方】，但公开公告列表未见 Easy/H5U 相关条目。安全研究覆盖度接近零。

---

## ③ FEL 与调试口知识卡

### 3.1 FEL（Allwinner BootROM USB 固件）知识卡

| 项 | 内容 | 来源 |
|---|---|---|
| 是什么 | FEL 是固化在 BootROM（掩膜 ROM）里的底层子程序，用于设备的初次编程与 USB 恢复，**先于一切可刷写介质存在** | linux-sunxi FEL 页 https://linux-sunxi.org/FEL 【社区·高】 |
| USB 识别 | 进入 FEL 后设备以 **VID:PID = 1f3a:efe8** 枚举（lsusb 可见；老系统会误显示为 "Onda V972 tablet in flashing mode"） | U-Boot 官方文档 Allwinner 页 https://docs.u-boot.org/en/v2024.10/board/allwinner/sunxi.html ；sigmdel.ca 实测记【官方+社区·高】 |
| 能力 | **读写任意内存地址、在任意地址执行代码**；dump boot0/U-Boot；对 SPI NOR 直接 `spiflash-info`/`spiflash-write`（BootROM 原生方式） | U-Boot 文档（"FEL protocol allows reading/writing arbitrary memory locations and starting execution at any address"）；xor.co.za 技术博客 https://xor.co.za/post/2018-12-01-fel-bootprocess ；sunxi-fel man 页 https://github.com/linux-sunxi/sunxi-tools/blob/master/sunxi-fel.1 ；electrodragon wiki 命令示例 `sunxi-fel -p spiflash-write 0 f1c200s.bin` https://w.electrodragon.com/w/Sunxi-fel 【官方+社区·高】 |
| 救砖价值 | **设备完全刷坏 SPI/NAND 后 FEL 依然可用**（ROM 不可擦除）；sunxi-fel 可经 Micro-USB 重写 SPI flash "bring a non-bootable device back to life" | linux-sunxi《Bootable SPI flash》 https://linux-sunxi.org/Bootable_SPI_flash 【社区·高】 |
| 进入条件 | BootROM 启动顺序 SPI→SD→NAND/eMMC→**全部失败自动 fallback FEL**；常见人为进入法：按住板上 FEL/RECOVERY 键、插"特殊 SD 卡"诱导（DSO2X1X 玩家即用 SD 卡让 F1C200s 进 FEL：EEVblog https://www.eevblog.com/forum/testgear/hacking-the-dso2x1x/400 ）、拉低 RECOVERY 引脚（Orange Pi Zero 实操 https://blog.runtux.com/posts/2021/10/17 ） | linux-sunxi FEL 页【社区·高】 |
| 工具 | `sunxi-fel`（linux-sunxi/sunxi-tools，**Debian/Ubuntu 官方仓库直接可装**：manpages.debian.org/unstable/sunxi-tools/sunxi-fel.1）；老牌 LiveSuit 也讲 1f3a:efe8 通道 | 【官方发行版+社区·高】 |
| 对本机的可及性推断 | Easy521 面板无任何 FEL 键【手册·01 笔记】。可行入口理论上只剩：① SD 卡诱导（前提：GE20-TF 扩展卡槽 B 接在 BootROM 的 mmc0 启动路径上——官方"SD 卡固件烧录"机制证明 SD 启动路径存在【手册+推断】，但槽位是否 boot 化未验证）；② 破坏 boot 介质让 BROM 自动 fallback（**等价于主动变砖再救**，绝不可在无 SD 恢复备份前尝试）；③ 若系统层有 shell，U-Boot 常规支持 reboot-to-FEL 命令（前提同样是已有 shell） | 【推断，全部标注为未验证】 |
| 安全机制覆盖 | 全志某些 SoC 支持 eFuse 安全启动（SBROM 验签），但**验签只管"介质启动"，FEL 执行通道通常不被禁用**；本机是否熔丝启用安全启动无公开资料，无证据表明汇川禁用了 FEL | 【社区·高，通用机制；本机适用性=未知】 |

### 3.2 调试 UART 知识卡

- 惯例：全志 BSP 平台的调试串口即普通 UART（boot0/U-Boot/内核三段日志都会输出），sunxi 社区板常用 **115200 8N1**；A40i/T3 工业 BSP 板卡（Forlinx 等）也是 UART 调试标配【社区·高：U-Boot/Forlinx 文档】。
- 位置惯例：OEM 产品无排针时，常见 4 焊盘空位（TX/RX/GND/VCC）在 SoC 附近或板边、贴纸/丝印覆盖、测试点形式。**无任何公开资料记载 Easy521/H5U 的 UART 位置或是否引出**——全网检索零命中（含中英文拆解帖）【本次调研结论】。
- 风险：① 电平未知（全志 SoC 常见 1.8V/3.3V 混布，直接怼 5V USB-TTL 可能损坏；A40i 多为 1.8V IO——**接错电平是真实损坏风险**，必须先量）；② 焊盘未必是 UART，可能是 JTAG/SD/FPGA 侧信号，乱接可能短路；③ OEM 常禁用控制台 shell，但**只听（RX-only）boot 日志也极有价值**：内核版本与编译选项、驱动加载序列（可印出 sunxi_usb_udc/EMAC/FPGA 通信驱动）、分区表、挂载表、FPGA 加载时序——不写任何东西就白拿一大堆信息。
- 与 FEL 的关系：UART boot 日志还能直接确认"槽 B TF 卡是否在 BootROM 启动顺序内"（日志会打印 boot device），这是 3.1 中最大未知的低成本验证途径。

---

## ④ 固件安全机制所知（签名/加密）

| 机制 | 公开所知 | 来源与可信度 |
|---|---|---|
| 固件升级验证码 | 编程手册明载"固件升级需**输入验证码**"（01 笔记 p971）。验证码与什么绑定（序列号/版本/全网通用）无公开资料；AutoShop 操作过程中验证码以何种形态上线（明文/摘要）无公开资料 | 【官方手册，机制黑盒】 |
| 文件格式 | **`.upgrade`（固件升级）/ `.img`（固件烧录）** 两类扩展名，由官方平台公开发放；内部结构（是否加密/是否含多分区镜像/是否打包 FPGA 位流）无公开资料 | 【官方，格式黑盒】 |
| 版本/配对完整性检查 | "先刷 PLC 包、再刷 FPGA 包，两包版本必须匹配，错配即故障"——说明升级链路存在**版本配对校验**（至少在 AutoShop↔设备握手层） | gongkong 官方排障 PDF【官方/半官方】 |
| 签名/加密启动链 | **零公开资料**：无 secure boot、boot0/U-Boot 验签、内核签名、根文件系统校验的任何公开记载；全志 BSP 默认不启用安全启动（需 eFuse 熔丝+产线烧录，工控 OEM 常省略） | 【社区·高（通用平台知识）+无公开反例】 |
| 篡改 .bin 的后果 | 通用 ICS 经验：改内容不改校验和→Bootloader 拒收→启动中断/变砖（CSDN 问答，泛化经验） | 【传言级】对本机适用性未经证实 |
| AutoShop 工程文件 | `.down`/`.updown` 可带密码（01 笔记 p972-983），工程层密码≠固件安全；无公开工程格式文档 | 【官方手册】 |
| PSIRT | 汇川有 PSIRT 上报渠道，但无公开 Easy/H5U 通告；侧面说明该产品线**未被公开安全研究触碰过** | 【官方】 |

小结：**没有任何证据表明 Easy/H5U 固件有公开记录的密码学签名/加密**；已确证的非密码学防线是"验证码+版本配对+机型族分包"。这既是研究机会（若真的无签名，FEL/自定义引导可行域更大），也是风险放大器（官方升级链一旦被错包打断，没有验签兜底直接变砖）。

---

## ⑤ 风险边界清单（哪些动作会变砖/损坏）

按危险度排序，**全部动作默认禁止在在线生产设备上执行**；用户自有实验机也需满足前置条件（标注 ★）。

| # | 动作 | 风险机理 | 严重度 | 前置条件 |
|---|---|---|---|---|
| 1 | **在线设备上做任何固件烧录/升级实验**（USB/以太网/SD 通道） | 手册明示：烧录清空应用程序、升级/烧录中断电"可能导致 PLC 无法启动"；错机型包/错版本对直接拒收或半砖 | 致命 | ★仅实验机；AutoShop 官方包；稳定供电；**先备好 SD 恢复卡（需 GE20-TF）**；记录原始固件版本 |
| 2 | **升级/烧录中途断电或拔线** | eMMC/NAND 写入中断→boot 链损坏；官方唯一救济=SD 卡烧录，没有 GE20-TF 卡就没有官方恢复通道 | 致命 | 全程 UPS/笔记本供电；USB 线禁用前置 hub |
| 3 | **自制/魔改 PhoenixCard SD 卡插入后上电**（B 槽） | SD 烧录为**上电自动触发**【手册 p121】，插卡即擦写，无确认步骤；错包=即死；这也意味着**任何人插一张卡就能刷你的 PLC**（自身即是物理攻击面） | 致命 | 卡只进实验机；量产卡制作只在镜像核对 MD5 后进行 |
| 4 | **以"让 BROM fallback 到 FEL"为目的破坏 boot 介质** | 等价于主动变砖；若 FEL 入口判断失误（非全志/熔丝锁定/端口未连出）则真砖 | 致命 | 永不建议；如做，仅限实验机+FEL 通道已验证可达+SD 恢复卡在手 |
| 5 | **调试焊盘带电乱接** | 电平误配（1.8V IO 怼 5V TTL）、把非 UART 焊盘当 UART（可能是 JTAG/SD/FPGA 侧）、短路 VCC | 硬件损坏 | 先断电万用表打点定位；先量 VCC 电平；首接只接 GND+RX（纯监听） |
| 6 | **FEL 模式下 sunxi-fel write 到任意地址/直接 spiflash-write 错镜像** | FEL 本身不会砖（ROM 在芯片里），但写坏 SPI/NAND 镜像即砖；无验签兜底 | 高 | 仅实验机；镜像来源官方；先 `spiflash-info`+`dump` 全量备份再写 |
| 7 | **对在线设备 USB 编程口做协议 fuzz/异常帧注入** | 运行时崩溃=产线失控（工艺安全问题，非变砖）；CDC 层畸形包行为未知 | 高（生产） | 只打实验机；在线设备只读监控（Modbus TCP FC03） |
| 8 | **Modbus TCP 写指令（FC05/06/0F/10）打到在线设备** | 从站默认开启、站号不检查【手册 p396-402】——写错地址就是改产线数据 | 高（生产安全） | 在线设备只读；写操作只在实验机 |
| 9 | **AutoShop 多实例同时烧录** | 手册明示只允许一个烧录实例（p961） | 中 | 单实例操作 |

（"★"前置条件组合：自有实验机 + 官方渠道固件包 + GE20-TF/SD 恢复卡预先制作并演练 + 全程监控供电 + 一次只变一个变量。）

---

## ⑥ P5 下一步侦察计划

### 6.1 一次"固件升级过程"的 USBPcap 抓包能拿到什么（零变砖风险，先做）

前提：实验机 + 从 InoPlanet 下载的**官方 `.upgrade` 包** + AutoShop 全程正常操作（这是官方支持的操作，不是攻击）。采集点：

1. **通道复用判定**：固件升级是否复用同一 CDC 接口（EP 0x02/0x81）与编程口共存/互斥——决定"编程协议"与"升级协议"是否同一协议栈（若同一栈，升级帧=协议样本的最佳放大镜）。
2. **枚举行为**：升级开始/结束时设备是否重新枚举、 VID/PID 是否变化（若中途出现 **1f3a:efe8** 即实锤全志 BootROM 参与了升级链——单条抓包直接证实 SoC 判定，价值最高）；若出现第二阶段 bootloader 的新 VID/PID，即可刻画"固件升级状态机"。
3. **帧结构**：握手序列、验证码字段上线形态（明文/哈希/挑战响应）、包大小分块、进度/擦除/校验阶段划分、错误路径（输错验证码时的 NAK 形态）。
4. **文件侧对照**：抓包总字节数 vs `.upgrade` 文件大小→判断是否加密/压缩（密文不可压缩、比例≈1:1；明文固件常有可压缩段或可见字符串）。
5. **对照样本**：同一抓包环境补一份"普通程序下载"与"Modbus TCP 读写"的样本，三方对比可分离出协议公共信封。

**拿不到的**：SoC 型号确证（除非出现 1f3a:efe8）、boot 链结构、UART 情况——这些必须线下。

### 6.2 必须线下（需用户配合项，按成本从低到高）

| 项 | 具体动作 | 能回答什么 | 风险 |
|---|---|---|---|
| L1 开盖拍照 | 拆实验机外壳，拍主控板正反面高清照（SoC/存储/FPGA 丝印、晶振、boot 电阻配置） | **SoC 判定终审**（A40i/T3 vs F1C200s vs 其他）；存储介质（SPI NOR 型号/容量）；FPGA 型号；有无预留焊盘 | 无（断电开盖即可）；注意保修涂层勿刮 |
| L2 UART 被动监听 | 定位 4 焊盘空位/测试点→断电量电平→GND+RX 接 USB-TTL（先只听不发送）→上电抓 boot 日志 | 内核版本/编译项、启动设备顺序（**槽 B TF 卡是否 boot 化**的免费验证）、驱动加载序列、是否留有控制台 | 低（纯监听）；唯一风险=接错电平，先量 VCC |
| L3 SD 恢复通道演练 | 购 GE20-TF 卡→InoPlanet 下载 Easy52X `.img`→PhoenixCard 制卡→**实验机**演练一次完整"烧录→恢复"循环 | 恢复通道可用性实测；顺便获得官方镜像实物供离线分析（binwalk 解包 `.img`——若未加密，整个根文件系统、内核、FPGA 位流、boot0 全部可离线尽览，**这是比 FEL 更安全的信息金矿**） | 中（这是真正的刷写操作，须在所有抓包/分析完成后、设备可牺牲心态下进行） |
| L4 FEL 可达性验证 | 仅在 L3 已演练成功后：`lsusb` 观察 + 尝试无卡上电看是否自动 fallback（若 boot 介质健康则不会进）→**不主动破坏 boot** | 1f3a:efe8 是否出现；sunxi-fel ver 输出（SoC 代码直证）；内存 dump boot0 | 中高（任何写操作都可能砖，只在有 SD 恢复兜底后做） |

### 6.3 纯纸面补充侦察（不碰设备）

- 补读 mydigit 拆解帖图片与日文 note.com 文章（本次抓取失败），比对丝印。
- 获取 InoPlanet `.upgrade`/`.img` 样本后离线 `binwalk`/`strings` 分析（合法：官方公开发放渠道）。
- 监控汇川 PSIRT 与 CNVD 是否出现首个 Easy/H5U 通告（一旦出现即有官方固件变更信号）。
- 全志 3.10 BSP 源码圈：F1C100s BSP 已在社区广泛流通（whycan 教程系），A40i/T3 BSP 经由 Forlinx/飞凌等方案商 SDK 流通——若 L1 判定出具体 SoC，可直接拿同源 BSP 做内核态符号对照（升级协议帧、驱动名、分区偏移全都有源码可对）。

---

## 附：本笔记引用来源汇总

**官方**：InoPlanet 固件页 https://www.inoplanet.com/developerResources/details/firmware-001-002 ；InoPlanet 资源主页 https://inoplanet.com/developerResources ；汇川 PSIRT https://www.inovance.com/portal/serviceSupport/psirt ；汇川官网下载 https://www.inovance.com/allResult?key=H5U ；汇川 Easy 产品页 https://www.inovance.com/product/details?productId=740 ；gongkong 排障 PDF http://sc19.gongkong.com/Upload/gongkong/technicalDataAttachment/201807/10/28fea753563c4ffcb4f96b699033da0b.pdf ；U-Boot Allwinner 文档 https://docs.u-boot.org/en/v2024.10/board/allwinner/sunxi.html ；sunxi-fel man https://github.com/linux-sunxi/sunxi-tools/blob/master/sunxi-fel.1 ；Debian 包 https://manpages.debian.org/unstable/sunxi-tools/sunxi-fel.1 ；Pine64 PhoenixCard https://docs.pine64.org 。

**社区·高**：linux-sunxi FEL https://linux-sunxi.org/FEL ；FEL/USBBoot https://linux-sunxi.org/FEL/USBBoot ；Bootable SPI flash https://linux-sunxi.org/Bootable_SPI_flash ；F1C100s https://linux-sunxi.org/F1C100s ；SoC Family https://linux-sunxi.org/Allwinner_SoC_Family ；Mainlining effort https://linux-sunxi.org/Linux_mainlining_effort ；xor.co.za FEL 分析 https://xor.co.za/post/2018-12-01-fel-bootprocess ；EEVblog DSO2X1X https://www.eevblog.com/forum/testgear/hacking-the-dso2x1x/400 ；electrodragon https://w.electrodragon.com/w/Sunxi-fel ；runtux FEL 入门 https://blog.runtux.com/posts/2021/10/17 ；CNX-Software A40i（2018-05）。

**社区·中**：whycan F1C100s BSP 编译 https://whycan.com/t_1014.html 、https://whycan.com/t_1522.html ；Forlinx T3 https://www.forlinx.net/product/t3-95.html ；3D Photonics A40i https://www.3dphotonix.com/som/a40i ；Geekbench sun8iw11 https://browser.geekbench.com/v4/cpu/baseline/15625520 ；知乎拆解 https://zhuanlan.zhihu.com/p/1948404513393337278 ；mydigit H5U 拆解 https://www.mydigit.cn/thread-278492-1-1.html ；note.com 日文调查 https://note.com/kzr8302/n/nb9e753bad070 ；sigmdel Orange Pi FEL 实测 https://sigmdel.ca/msepa/posts/… ；aw-ol Tina 文档 https://docs.aw-ol.com 。

**传言（不采信/仅存档）**：淘宝 H5U"瑞萨 RA4M1 主控"话题；CSDN 内容农场固件升级文 https://blog.csdn.net/gitblog_09803/article/details/142951980 。
