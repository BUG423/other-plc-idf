# Linux 原生条件 ST、绑定、真机配置与命令入口集成

2026-10-19。统筹从仓库公开的两份带注释 ST 重新执行真实构建，完成 `MatIEC → 原样生成C → Clang ARM → 两级LLD → 同工程GVT绑定 → 18文件候选 → 17文件本机配置派生 → 原生下装CLI预览`。[集成元数据](../../../evidence/easy521-native-matiec-20261019T075356Z/summary.json)记录源码、对象、清单、测试和分发哈希。本批没有发出 PLC 写命令，也未把下装预览提升为设备接受。

## 程序与本机验证

[conditional_plus1.st](../../../examples/easy521_native_memory/conditional_plus1.st)在值小于10时加1，否则清零；[conditional_plus2.st](../../../examples/easy521_native_memory/conditional_plus2.st)改为加2。它们使用固定既有 `g_rBESS_Pcmd:REAL`，实验值无功率单位。任务周期、初始化与保持继承原配置，未知语义均在源码说明。

两份新 MAIN 都是52字节 Thumb 函数，分别具有不同对象哈希。真实 MatIEC 负责语法、类型及分支生成；适配器只绑定既有外部变量与导出入口，没有手写分支 IR。统筹使用同一生成C和适配器在 Linux/x86 各运行12次，再将输入设为NaN运行一次：

- +1：`1,2,3,4,5,6,7,8,9,10,0,1`，NaN分支输出0。
- +2：`2,4,6,8,10,0,2,4,6,8,10,0`，NaN分支输出0。

这些结果验证主机语义、连续调用和外部值没有每次被初始化。没有执行 ARM 指令，不能当PLC扫描实测。编译器及适配器范围见[31](31-matiec-native-arm-2026-10-19.md)，绑定及29项实际C依赖见[33](33-matiec-bound-application-2026-10-19.md)。

## 应用候选与实际配置

原有分配、任务接口、SBR和两库继续固定。新的两个ARM对象进入18文件候选，再与[本机17文件读回](32-native-application-readback-2026-10-19.md)核对：用户程序和变量分配解压后逐字相同；12项配置业务payload和版本相同，仅不透明头字段有两个字节差异。派生清单中15个非ARM文件逐字保留本设备读回，只换两个新对象，不把不透明头补零。

| 源码 | 绑定后 stprog.o SHA-256 | 17文件清单 SHA-256 |
|---|---|---|
| conditional_plus1.st | `7751d52e968304c16144d487900c350061d4e2727c96045219d1cc54a3a7936d` | `ce5f75a77680e25da792a9491b0b77c4504052f10cc02908e2537e4d7618cc33` |
| conditional_plus2.st | `0e6f841f36e0b94a7519651d3db81c5d39a688fd71b6ea6f112097c8819bd5af` | `ec70a8fb7f147a54b0c7cd9665f5feb948bbfd250571e053156dd8ce6d2c2a17` |

两份最终清单位于私有 `.plcidf/native-integrated-matiec-20261019T075356Z/plus{1,2}-live/live-application-candidate.json`。源码归档先前读取error132，因而17文件方案不声称其缺失、可省略或已满足厂商归档策略；符号源码MD5继承现机，不声称与新源一致。证据及压缩器见[29](29-entry-symbol-and-readback-audit-2026-10-19.md)、[30](30-live-application-config-2026-10-19.md)。文件闭包候选与受控设备验收保持分开。

## 当前实际命令

聚合绑定入口显式选择MatIEC，无需旧 `--llc` 参数。下列台架路径是本项目已有合法输入，迁移机器时须自行提供对应固定工程和库；原始厂商输入不随仓库发布。

```bash
python tools/bind_native_application.py \
  --compiler-backend matiec-clang \
  --source examples/easy521_native_memory/conditional_plus1.st \
  --globals-path examples/easy521_native_memory/globals.json \
  --project-dir /mnt/e/PLCProjects/PLCIDF_LAB_20261017/BESS_E521_DEV \
  --syslib .plcidf/research-16-link-20261019T014840/inputs/SysLib_New/_SysLib.o \
  --stclib .plcidf/research-16-link-20261019T014840/inputs/SysLib_New/_STCLib.o \
  --matiec-root /home/zss/.cache/plcidf/matiec \
  --clang /usr/bin/clang-20 \
  --lld .plcidf/research-tools/lld20/extracted/usr/lib/llvm-20/bin/ld.lld \
  --output-dir .plcidf/a-new-matiec-build
```

配置组合与现机派生参数见[28](28-fixed-application-config-2026-10-19.md)、[30](30-live-application-config-2026-10-19.md)。新原生命令默认预览：

```bash
python tools/easy521_application_usb.py run
python tools/easy521_application_usb.py stop
python tools/easy521_application_usb.py download \
  --manifest .plcidf/native-integrated-matiec-20261019T075356Z/plus1-live/live-application-candidate.json \
  --credential-hex 0000000000000000 --linux-device 001:002
```

RUN/STOP预览不枚举或读取设备；download预览只读取本地清单与载荷，严格检查常规文件、顺序、长度、哈希、对象关联和读取前后身份，不取设备lease、加载libusb或生成随机序列。统筹实际运行了两份新清单预览，均 `device_access=false`。显式执行另需当前设备身份、可访问且空闲的USB接口；主机准备不由工具静默触发。执行行为、ACK与unknown边界详见[35](35-cli-native-application-2026-10-19.md)。

## 未通过的设备验收

新内存 MAIN 没有厂商中断分发钩子；保持配置字节不等于保持全部调度语义。灯程序的真实READ_BITS/WRITE_BITS描述符和中断调用证据见[34](34-native-io-abi-2026-10-19.md)，本轮没有据此猜测BOOL/物理I/O适配或向Y0写入。

下一次真机下装前，已询问当前Y0～Y7是否仍全部悬空，尚无新回复。当前保留原应用及前述读回备份，待现场信息后执行一次受控候选下装，分别观察设备字节、RUN/STOP与规则变化。当前不支持任意IEC程序、定时器、保持局部量、完整中断调度或通用机型。

本批全工具回归通过，独立审阅213项替身测试通过；46项Python资源经过sdist→wheel→全新venv→仓库外预览，逐项哈希与当前源匹配。MatIEC/Clang真实编译测试已加入CI；前批 `266eaf6` 的software/open-linux均成功。软件测试与独立安装不替代上述真机验收。
