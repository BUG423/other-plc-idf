# 路线 3：目标硬件证据表与离线固件检查器

日期：2026-10-17。交付为可运行的通用静态检查工具与采样规范，适用于正常取得的本地镜像、对象文件和设备资料；本轮未连接 PLC、访问 Windows 进程、升级或替换设备系统。**Easy521 的实际 SoC、OS、BSP 与板卡 I/O 接口仍为 unknown。**

## 已实现与复现

运行 [inspect_firmware_image.py](../../../tools/inspect_firmware_image.py)：

```bash
python3 tools/inspect_firmware_image.py /path/to/normally-acquired-firmware.img
python3 tools/inspect_firmware_image.py /path/to/large-image.img --max-bytes 134217728
python3 -m unittest discover -s tools/tests -p test_firmware_image.py -v
```

工具只使用 Python 标准库，stdout 为一个 JSON 文档；退出码 `0` 表示完成规定范围的检查，`2` 表示输入或读取错误。`ok=true` 不代表镜像合法、目标型号匹配或设备可启动。

| 内容 | 实现的检查与输出 | 证据边界 |
|---|---|---|
| ELF32/64，大小端 | 文件头、程序/节表和文件区域边界；跳过不占文件空间的 `SHT_NOBITS`；记录 machine、OSABI、位数与字节序 | 部分结构检查，未验证全部链接语义、重定位、符号或 ABI；目标指令集不等于实际芯片型号，OSABI=0 不证明 Linux |
| Legacy uImage | 64 字节头、头 CRC32、声明长度、完整可见载荷 CRC32、OS/架构字段 | Linux 字段只是镜像自报目标；CRC 不是签名，不证明来源、板卡适配或可启动性 |
| DTB v17 | 头与区域布局、保留区终止符、节点/属性 token 与边界；读取有界 `compatible`、`model` | 得到镜像配置声明，不是连接板卡的实测身份；不把某个兼容字符串自动登记为已确认芯片 |
| 压缩包与平台文本 | 7z/xz/gzip/zip 魔数；Linux、U-Boot、sunxi、Allwinner 等文本位置 | 仅低级线索，不解包、不解密，也不把任意字符串提升为硬件或运行 OS 结论 |

每项结果带 `offset`、实际取证 `length`、对应字节的 SHA-256、`evidence_grade` 与 `scope=offline_file_only`。声明长度独立记录为 `declared_length`；截断或载荷 CRC 失败时明确降级。DTB 属性值位置是相对于该 DTB 的 `relative_value_offset`，文件绝对位置需加外层 `offset`。多个结果可能互相嵌套，并非独立分区。

默认只读文件前 64 MiB，可调上限 256 MiB；超过范围时 `input.limited_to_prefix=true` 且完整文件 `sha256=null`，仅记录已检查前缀哈希。另有限制：最多 4096 个结构候选、256 个默认结果、16 MiB 单 DTB、共享验证字节与 token/表项预算。达到预算时 `limits_reached=true`，报告不能当作完整穷举。拒绝目录和 FIFO 等非普通文件；不加载输入为代码、不启动外部分析程序、不写输入、不访问硬件。

没有找到魔数不能证明加密或无 Linux：压缩、封装、分段、未知格式及检查范围均可能影响结果。v17 以外的 DTB、扩展 ELF 表计数、原始 zImage、厂商升级容器、文件系统与 FIT 组件提取尚未实现；不宣称完整固件解析能力。

本轮验证结果：**35 项离线测试通过**，覆盖不存在输入、空文件、截断、头/CRC/区域误报、大小端、嵌入偏移、前缀与工作预算、普通文件限制，以及 300 个固定种子的格式变异样本。

还实际读取了两份文件，均未执行输入：

| 样本 | 本轮工具结果 | 输入 SHA-256 |
|---|---|---|
| 仓库 `plc/easy521/autoshop/BESS_E521_DEV/STTemp/MAIN.o` | offset 0，34284 字节，ELF32 小端，ARM，ET_REL，OSABI 0；硬件身份 unknown | `10b84e22569143dd04e24490d8d33d05b004fa383f60e3350fc7a20988627ea9` |
| 本机 `/bin/true`，宿主对照样本 | offset 0，26936 字节，ELF64 小端，x86-64，ET_DYN，OSABI 0；硬件身份仍 unknown | `8a63c98320173f79e263115e10cefc170155e47d1fd4b5a70e4429d2699cae28` |

这两个样本不是 Easy521 系统固件。真实 Easy521 官方固件仍未取得，不能把合成测试或宿主文件登记为目标固件解包成功。

## Easy521 硬件证据表

| 目标项 | 当前状态与直接依据 | 还需取得的证据 |
|---|---|---|
| 产品身份与固件版本 | 既有真机信息记录 Easy521 / 6.11.0.1；范围见 [实测记录](../../management/LIVE_VALIDATION_2026-10-17.md) | 完整订货型号、序列号与板卡修订之间的对应，内部资料可脱敏保存 |
| CPU 指令集 | 上述归档 `.o` 为 ARM32 编译目标 | 对应实际设备 runtime 的加载与执行链证明；应用目标不直接锁定 SoC |
| CPU 拓扑/SoC | 官方 Easy521-0808TP-INT 型号页声明四核；实际芯片型号 unknown | 与当前板卡对应的厂商 BOM/原理图/型号声明，或可辨认丝印及来源记录 |
| OS / 内核 | unknown；历史 USB 自述有 Linux/sunxi 线索，但不构成独立终审 | 正常取得且确认型号版本的官方镜像、许可清单、启动资料或经核对的板级系统信息 |
| Bootloader / 引导策略 | unknown | 对应板卡的官方恢复说明、引导链/分区资料及恢复可验证性 |
| BSP / 设备树 | 未取得匹配当前板卡的资料 | BSP 版本、源码/许可、设备树与板卡修订对应；通用芯片 BSP 不自动覆盖 OEM 板卡 |
| 数字/模拟 I/O 与协处理器 | 寄存器、总线及驱动映射 unknown；不假定必有 FPGA | 官方接口或驱动、硬件连接与故障输出契约，以及后续独立台架时序验证 |
| 签名/加密与 secure boot | unknown | 厂商说明和正常取得镜像的可验证结构；没有资料不意味着没有保护 |

四核声明来自 [Easy521 官方型号页](https://www.inovance.com/global/content/details_816_556159.html)；固件获取状态与条件见 [04 官方来源复核](04-official-sources-2026-10-17.md)。设备友好名、文件名、USB VID/PID 与可配置 USB 字符串均不能单独确认内部芯片；Linux 上游明确允许配置 gadget 自述字符串。[Linux gadget configfs](https://docs.kernel.org/usb/gadget_configfs.html)

## 向其他目标复用的采样方法

每个目标单独维护“产品/板卡身份 → 资料来源 → 镜像 SHA-256 → 镜像内部声明 → 独立硬件证据”的链条。至少记录目标完整型号与修订、资料版本/发布日期、获取 URL/本地来源与条件、原始文件长度和 SHA-256、采样命令/工具版本、偏移与长度、观察事实、推断、未知项及核对人。样本若超出本工具读取范围，完整源文件哈希应另行流式计算并与前缀哈希明确区分。

先取得正常渠道的原始包并保留原件，对副本作离线检查；如经授权使用其他解包器，将外层包、解包工具/版本和各输出的哈希关联起来。允许下载不自动等于允许修改/反编译；取得物的使用条件单独登记。板卡开盖、上电串口、引导模式与刷写不属于本工具，不能根据外观猜接线或以另一机型恢复流程替代验证。

向厂商获取的材料应明确到目标修订：当前固件对应恢复包；Linux 等组件的开源许可与源码获取入口；板级 BSP/设备树和 I/O 驱动接口；原生 SDK 的宿主架构、许可与机型范围。对其他厂商或 CPU 家族复用相同证据表，但重新核实这些资料，避免把一种产品的软件栈推广到全部 PLC。

格式依据为一手规范：[ELF 文件头](https://gabi.xinuos.com/elf/02-eheader.html)、[U-Boot legacy image 定义](https://github.com/u-boot/u-boot/blob/master/include/image.h)、[Devicetree v17 扁平格式](https://devicetree-specification.readthedocs.io/en/stable/flattened-format.html)。这些规范只解释镜像字段，不提供 Easy521 的板级身份或 I/O 控制契约。
