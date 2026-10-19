# Easy521 原生应用文件协议：真实 H530 上传、下载与 GCM 写入链

2026-10-19。本轮只读本机合法安装的厂商主机二进制及已有工程/设备记录，使用 Linux `objdump` 与标准库 PE 解析；没有执行 Windows、加载厂商 DLL、启动 Simulator 或操作设备。模块、局部代码范围的 SHA-256、调用链和验收计划见[结构化记录](26-native-application-protocol-2026-10-19.json)。原始二进制与完整反汇编留在本地私有目录。

**本轮交付可供独立审阅的原生应用文件读取器，并恢复了有序下载所需的精确命令子集。** 这不是完整 Linux ST 编译器、已完成的真机文件备份或已经通过的原生下载。25 号实际固定读取成果不自动外推为本文件协议通过。

## 从真实调用链恢复，未把 Simulator 当设备协议

以各 PE 的首选基址为准，InterfaceMgr/H530 基址均为 `0x10000000`；下表地址为 RVA。

| 操作 | InterfaceMgr facade → 实现槽/函数 | H530 实际命令执行 |
|---|---|---|
| 上传到电脑，文件路径重载 | `68fc0 → +104 → 5c10` | `CUploadFileCommand` 虚表 `7c20c`，执行槽 `+3c=1f890 → 1fce0` |
| 下载到 PLC | `68ef0 → +fc → 5ac0` | `CDownloadFileCommand` 虚表 `79d3c`，执行槽 `+3c=d130 → d440/d7a0` |
| SetGCMState | `68a10 → +a8 → 50d0` | `CGCMStateCommand` 虚表 `79e54`，执行槽 `+3c=dcb0 → de00 → c7d0` |

虚表以 RTTI complete-object-locator、构造器写入和执行槽交叉确认，未把共享析构函数当执行入口。AutoShop 的上传 IAT `0x1eb1738` 有实际调用；下载仍沿16号报告的有序文件 vector。上传器对远端数据使用文件 selector `4`，不是对 ELF 地址进行猜测性内存读取。

## 普通帧与文件布局

公共构造器 `28050` 生成10字节头：LE16总长、版本1、selector、LE16序列号、清零的两字节保留字段、operation、普通flag0。每帧序列号递增并以16位回绕。序列关联不构成认证或唯一CPU身份。

重新查阅全DLL中对 protocol `+6a/+6e/+72` 的写入：除基础构造清零外，启动一次性flag覆盖的代码在另外的命令执行链 `f76e/f84b/100dc`；本报告的 Upload/Download/GCM 执行链没有设置覆盖。实现只接受普通零flag请求，不加入未经恢复的登录、站号或selector高位语义。响应保留未知的reserved/flags，但不会由这些未知字段扩大允许操作。

文件打开正文由 `1fa00`（上传）与 `d300`（下载）恢复：

| 完整帧偏移 | 字段 |
|---:|---|
| 10 | 8字节、调用方提供的窄字符密码缓冲区前缀 |
| 18 | LE32块字节数 |
| 22 | LE32文件字节数；上传读取固定写0，下载写实际长度 |
| 26 | 窄字符远端路径，结尾NUL |

上传发送 selector4/op1，下载发送 selector4/op2。**op2是破坏性打开，不能用于“先试着看看文件”。** 打开响应由 `288c0` 接受 op6/一个LE32；上传把该值用于后续读取总长度。op15/一个LE32始终是错误，即使错误数值为0或1，也不能当成功长度。

USB派生构造器 `295d0` 明确将读块上限设为 **494**、写块上限设为 **40960**，覆盖基础类的1000字节默认值。不得依据一次bulk最大512B就把文件写块上限也改成512。

| 阶段 | 请求正文 | 正常响应 |
|---|---|---|
| 文件读取 op6 | LE32零基块序号 | op5：LE32块序号、LE32数据字节数、数据；最大完整帧512B |
| 文件写入 op5 | LE32零基块序号、LE32实际块字节数、数据 | op6/一个LE32确认信息 |

`280d0` 的分包模式3按已知文件长度生成读取块序号，模式2为写入构造块号/长度/数据。厂商分配写块时多留一个清零字节，但传入公共构造器的长度不包含该字节；实现不额外传NUL、CRC或猜测性校验和。上传数据组装 `28660` 从完整返回帧偏移14读取块长，从偏移18复制数据。这些是厂商主机自己的请求/响应消费证据。

## 凭据、路径与路由的边界

实际上层调用传入空CString，命令构造器也初始化空密码字段；这证明有空凭据调用方式。H530 IAT74030实际调用InterfaceMgr的`EncodingManager::ConvertToCStringAFromUnicode_BaseOrUTF`，返回`HString`；IAT7408c为`HString::GetBuffer`。InterfaceMgr `5e4c0`最终经MFC90u ordinal310构造该窄字符对象，`5b430`再转发到ordinal2697的GetBuffer。厂商打开正文仍直接从窄密码缓冲区复制8字节，因此**空字符串不能独自证明其后7字节的历史线值，也不能证明所有Easy521都免密码**。新API要求调用方显式提供8字节，不设默认、不轮试密码、不发送登录/密码修改命令。CLI里的全零8字节只可由统筹结合本机已知配置明确选择，并作为待真机验证的凭据编码记录。

远端只允许16号真实应用表中的精确basename，例如 `userprog.foid`、`stprog.o`、`varsinfo.foid`。尚未得到本设备前缀的真机字节证据，代码不自动补 `/am600-nand/`、`default` 或 `PrgBackup`，也不在失败后改路径重试。指定basename并不保证当前设备上一定存在该文件。

真实H530上传执行序列只有open1和read6，没有op2/op5或SetGCM；这支持“请求读取已有文件、没有请求写内容”的操作范围。Simulator `_file_route_create` 对op1使用读取路由，对op2存在`ftruncate`，其read在完整读取后销毁route；这是额外交叉证据，不是Easy521固件源码。Simulator的password检查直接返回true，**绝不能将它当成设备密码策略**。

本版本H530上传尾部没有发送额外close命令。新实现相应不猜测op15清理请求；主机句柄/接口会释放，设备route清理状态保留unknown。失败或大小超限后不会自动重新打开、补发、猜测close或升级为下载。

## SetGCM 与有序下载

`de00 → c7d0` 恢复出精确GCM写帧：selector1、op2、正文LE32 `ff000000`、LE32长度1、状态字节0～3。正常响应op6/一个LE32；厂商共同写函数把“op6成功”与确认值分别处理，没有要求确认值恰为1。新实现保留原始确认值，不能把它当CPU状态读回。

16号上层顺序现可对应到线帧：**GCM raw2 → 按review后的文件表逐项open2/write5 → 全部成功后GCM raw3**。0/1具备此前的STOP/RUN映射；2/3仍只报告原始事务值，不擅自把ACK命名为“原子提交成功”。写入失败即停止，不自动发送raw3、RUN、回滚或重放。完整文件闭包、型号/版本条件、激活时机和失败恢复仍分别验收。

## 实现、验证与首个真机计划

[离线codec](../../../tools/easy521_application_protocol.py)严格区分只读打开、破坏性打开、读块、写块和GCM写入。[原生事务模块](../../../tools/easy521_application_usb.py)复用25号严格CDC描述符检查，持有同一USB句柄完成一个文件的读取，限定lease、单请求OUT、共享IN截止时间、会话截止时间、最多8MiB单文件及全新本地输出。读取会话拒绝所有writer；下载和CPU控制是单独显式API，不从读取器触发。

预览示例，**不枚举USB、不加载libusb、不检查输出路径、不获取lease**：

```bash
.venv/bin/python tools/easy521_application_usb.py \
  --remote-name userprog.foid --credential-hex 0000000000000000
```

首个真实实验由统筹独占执行：核对当次USB身份并显式准备主机权限/driver；单独读取GCM；选择已知凭据编码；对小文件 `userprog.foid` 只执行open1/read6，保存到全新私有文件并计算SHA256；任何错误立即停止、保留原始bytes和unknown；finally释放主机资源后，再用既有固定reader独立读取GCM。CLI执行需显式 `--execute --linux-device BUS:ADDRESS --output-path 新私有路径`，不得照搬旧地址。

90项新增测试通过，均是独立构造帧和fake libusb C-ABI，不访问设备。覆盖精确494/40960布局、无额外尾字节、逐块长度/序号、设备错误、busy/permission、部分或零字节IN/OUT超时、rc0短发送unknown、中断保留实际report/退出130、不重放、同一接口生命周期、旧文件保护、read会话拒绝writer及下载失败不提交/重启。测试计数只说明本轮软件验证，不与25号既有整套重复相加。

```bash
.venv/bin/python -m pytest -q \
  tools/tests/test_easy521_application_protocol.py \
  tools/tests/test_easy521_application_usb.py
```

当前没有新增真机应用文件读取/写入、完整ST编译、完整备份恢复或应用执行通过声明。

本轮另给既有固定内存reader补充 `pcmd`：`g_rBESS_Pcmd`、REAL32、opaque地址`0x021000f0`，独立来源为冻结CSV第275行和GVT，关联17/27号分配记录。Windows helper镜像同一固定项，仍只读，不开放任意地址或写操作；它用于下一步将新应用源码变化与独立变量回读关联，不单凭该值证明完整应用身份。

## 同日大文件实测暴露的512字节/ZLP边界

统筹另一次新会话读取`stprog.o`时，open返回125848字节，块0成功收到完整512B（494B数据），块1只发一次OUT后首个IN返回`rc0/count0`。旧实现立即失败并保留unknown；原失败记录 `.plcidf/native-execution-backup-20261019T072430Z/02-application-stprog.o.json` 不覆盖。该现象与上一个整包后残留终止ZLP相符，但不能把所有空传输都解释为成功。

修正只允许同一句柄session中，在上一个已关联且成功的响应恰512B、最后一次IN也恰512B时，下一请求在尚未收到字节之前消费一个`rc0/0B`。它在read_calls中记录`accepted_prior_full_packet_zlp=true`，消耗原4次IN预算、使用同一个接收deadline，不再发OUT、不重连或重放。没有此前证据、第二个ZLP、部分帧中间的空传输以及所有错误/超时仍失败。新增fake验证完整512→ZLP→合法下块、无前证据拒绝、重复ZLP拒绝、deadline不延长。规则不跨新session继承，也未加入猜测性尾部drain。

USB传输长度与设备packet边界的区别依据[libusb官方说明](https://libusb.sourceforge.io/api-1.0/libusb_packetoverflow.html)；short packet和零长终止机制参照[Linux官方Host API](https://docs.kernel.org/driver-api/usb/usb.html)。这些通用资料解释传输机制，具体将本次0B归属给前一响应仍属于待后续读取验证的受限推断。

进一步只读MFC90u静态链定位到ordinal310构造及ordinal2697 GetBuffer，未运行其代码。它只能保证空字符串有NUL，不能证明历史8B拷贝的后7B恒零。统筹报告本设备显式zero8读取`userprog.foid`成功，可作为本设备当前凭据候选被接受的独立事实，不将其升级为其他设备的免密码规范。后续真机验收由统筹在新记录中报告。
