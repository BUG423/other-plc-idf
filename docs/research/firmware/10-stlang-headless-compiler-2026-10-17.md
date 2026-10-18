# StLang 独立主机编译实测（2026-10-17）

在隔离 Windows 工作目录中，已经实际脱离 AutoShop GUI 调用旧、新两版 `StLang.exe`，把已有厂商 `STTemp/MAIN.st` 和空 `MAIN.cl` 编译为新产生的 ARM 对象文件。重复编译的同版 `.o` SHA256 完全相同。这打通了 **厂商 STTemp 中间输入 → ARM `.o`**，尚未打通普通 IEC 文本的项目预处理、完整程序链接、下载或设备执行。

原始 JSON 和二进制副本仅保存在本机；可复用主机工作进程已实现为 [PowerShell helper](../../../plc/tools/compile_vendor_st.ps1) 与 [Python 入口](../../../tools/compile_vendor_st.py)。公开[证据元数据](10-stlang-headless-evidence-20261017.json)记录了 22 次调用、进程编号、输入和产物哈希、状态与诊断。所有调用采用新的目录和自己创建的命名映射，每次最多等待 4 秒，只允许终止自己启动的进程。未操作原工程、AutoShop GUI、通信 DLL 或设备。

## 实际接口

该程序不是 `StLang.exe input.st output.o` 类型的文件参数 CLI。`--help`、无参数和数种文件参数调用都在约几十到百余毫秒内静默返回 0，没有新产物。实测有效形式为：

```text
StLang.exe Local\plcidf_STLang_<唯一名字> 0 0 0
```

第一个参数是调用方创建并保持打开的 Windows 命名文件映射。其余三个整数分别流向工程编码、ST 兼容设置和编译选项入口。本次只验证三个 0 的组合；没有确认其他合法值。实际路径均为 ASCII，不能据此宣称已支持中文路径。

调用方先填充 64 KiB 的映射，再启动进程，保留映射直至读取完成状态及收集产物。实测最小数据结构如下，整数为小端，长度均为字节数，不含自动添加的终止符：

| 次序 | 数据 |
| --- | --- |
| 1 | `u32` 工作目录长度，然后是目录字符串字节；目录须已经存在 |
| 2 | `u32` 配置字符串长度，然后是配置字节；本次为空 |
| 3 | `u32` 后续所有任务记录的总字节数；不是任务个数 |
| 4 | `u8` 全局标志；本次为 0 |
| 5 | 一个或多个任务记录；本次只验证一个 |

单个任务记录顺序为四个 NUL 结尾字符串 `MAIN`、`ST`、`MAIN.st`、`MAIN.o`，然后 `u32 fileID=0`、`u8 programFlag=1`、`u8 debugFlag=0`、三个初始化为 0 的可写状态字节。四个字符串在本例中的角色分别为任务标识、语言段、输入名、对象输出名；第一个字符串的一般约束仍未验证。三个状态字节由进程写回 started、done、success，本例记录长度 32 字节。

早期运行原型在 `/tmp/plcidf-stlang-ipc-cl-probe.ps1`；仓库中的可复用 helper 使用固定已检查安装路径和编译器 SHA256，并通过 Windows Job Object 在 helper 关闭时清理编译子进程，不加载通信接口。隔离根目录为 `E:\PLCProjects\PLCIDF_STLANG_HEADLESS_20261017_144238_504888a2`。复现时须使用新的目录及自己的合法输入，不能把该旧中间输入当成当前设备程序。

## 已验证的正负结果

| 输入/调用 | 两版共同结果 | 结论 |
| --- | --- | --- |
| `--help`、无参、普通文件参数 | 返回 0；无诊断、无新文件 | 不能把返回 0 当成功编译 |
| 有效映射，只复制 MAIN.st | 新生成 `.rst`，报告缺少 MAIN.cl；状态 `[1,1,0]` | 已进入真实编译处理 |
| 有效映射，合法 MAIN.st + 空 MAIN.cl | 新生成 ARM `.o`；`.rst` 为 Success；状态 `[1,1,1]`；没有错误诊断 | 独立中间输入编译成功 |
| 同一输入在另一个新目录重编译 | 同版 `.o` 哈希完全一致 | 排除复用旧产物；本例可重复 |
| 自写普通 IEC PROGRAM 文本 + MAIN.cl | lexer 语法错误；没有 `.o`；状态 `[1,1,0]` | 不能直接以普通文本替换厂商 STTemp 容器 |
| 32 字节空厂商容器 + MAIN.cl | 先输出 lexer error，之后仍给 success、Success `.rst` 和 `.o` | **状态/结果文件也不足以单独判断成功** |

两版的有效映射调用在成功、缺文件和语法失败时均返回 **1**。独立适配器必须综合进程是否按时完成、完整 stdout/stderr、任务状态、新产物、ELF 目标、输入稳定性和预期入口符号判断；任何 lexer/syntax error 均应失败，即使之后报告 Success。不得按照常见的“返回 0 成功”处理此私有接口。

正例输入 MAIN.st 为 1715 字节，SHA256 `0b9521fa530a0e6fcd119fbe57ef5fb78bfc92742a9670f0cbd1d30788ab82e5`，含厂商容器头；未尝试解密、伪造该容器或去掉保护。它来自此前复制的工程中间文件，不代表最新在线 probe。

| 编译器 | 新 MAIN.o 字节数 | SHA256 |
| --- | ---: | --- |
| STLang | 3900 | `221101871d7177b3a71f6f2271b52550053bc171e9b7b4a322da7f771209d1ba` |
| STLang_New | 3948 | `b74fd3a84f911f612be9d17c8ce54e733ae133aa65a97c14d740130b64053bde` |

`file` 和 `readelf` 均确认这两个新对象为 ELF32 little-endian、ARM、EABI5、可重定位目标；属性为 ARMv7 Application profile、Thumb-2、VFPv3、NEONv1；均有 `MAIN` 函数和该输入预期的未解析全局符号。stdout 的 `i686-pc-windows-msvc` 是主机相关输出，不能据此把生成目标判成 x86。对象目标也不能反推实际 Easy521 SoC、Linux、设备 ABI 完整兼容或可直接下载。

## 工程集成边界

已经实现实验性 Windows 编译工作进程：只接受当前两版已检查的安装路径及其 SHA256，复制冻结 MAIN.st/MAIN.cl 到必须不存在的新构建目录，拒绝输入/输出嵌套、安装目录、重解析点、普通 IEC 文本和超过 32 MiB 的文件。输出结构化诊断、对象哈希和有界 MAIN 入口符号检查；Windows 端和 Python 端都会复核实际文件。它保留 Windows 编译器依赖，可由 WSL 发起，不占用 AutoShop GUI。

```bash
python tools/compile_vendor_st.py \
  --compiler 'E:\Software\AutoShop\Bin\STLang_New\StLang.exe' \
  --input-dir 'E:\PLCProjects\frozen_STTemp' \
  --build-dir 'E:\PLCProjects\fresh_object_build' --timeout-s 4
```

Python API 为 `compile_vendor_st(compiler_path, input_directory, build_directory, *, timeout_s=4, powershell_exe="powershell.exe", helper_path=None) -> dict`。PS 参数为 `-CompilerPath/-InputDirectory/-BuildDirectory/-TimeoutMs`；默认超时 4000ms，上限 30000ms；仅实现 MAIN 单元、ASCII 本地盘路径。helper 包装进程用通常的 0/1 表示成功/失败，原 StLang 的返回值单独保存在 `exit_code`，不可混淆。JSON schema 为 `plcidf.vendor-object-build.v1`，scope 固定 `vendor-object-only`；`object_files` 里的 SHA256 不等同完整设备应用哈希。

自动化负向/结构检查共 31 项通过。发布 helper 的真实 Windows 测试已验证旧版正例、新版经 Python 调用正例、lexer error 后伪 success 被拒绝、普通文本在预检查被拒绝。所有真实输入均来自隔离冻结目录，未向当前原生工作工程写入。

要完成用户普通 ST 文本一键下装，还须取得或实现厂商项目预处理契约、全局变量/库/任务配置与链接封装，以及应用下载和运行态读回接口。当前没有证据证明只运行 StLang 就已经覆盖上述步骤；不应据此把真正的无 GUI Easy521 工程编译部署标为完成。

## USB 单独供电与 RUN 的资料复核

核查了安装包中的 Easy 用户手册（177 页）、中英文编程手册（1022/1084 页）和版本说明（49 页），以及 880 个 XML/INI/TXT 资源。用户手册第 138 页的明确限制为“当只有USB供电时，不支持掉电保持”；未找到 USB 单独供电必定禁止 CPU RUN 的明文结论。其他 24V 供电错误命中来自扩展模块，不可移作本体限制。

`AutoShop.exe`、`ChsRes.dll`、`CommunicaterModule.dll` 中确实存在现场的通用运行切换失败文案，未取得其原因枚举。`GetPLCRunMode` 的导出签名显示状态通过 `unsigned long*` 输出，不能从导出名恢复数值含义；本轮没有调用它。当前 USB-only 下请求 RUN 失败是现场事实，供电是否为根因仍须以正常供电下同程序和独立设备状态读回交叉核实。手册及资源文件哈希见元数据；未向仓库复制厂商文档或原始资源表。
