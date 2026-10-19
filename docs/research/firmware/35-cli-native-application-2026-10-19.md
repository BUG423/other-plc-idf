# 35. 原生 Linux 应用与控制命令入口（2026-10-19）

[easy521_application_usb.py](../../../tools/easy521_application_usb.py) 现在提供 `read`、`run`、`stop`、`download` 命令。原有不带子命令的 `--remote-name` 读取方式继续兼容。各命令默认预览；下载预览只读取指定的本地候选清单和文件，读取及控制预览不检查输出路径、不访问本地业务文件。所有预览均不获取设备租约、不加载 libusb、不枚举 USB、不生成随机事务号或写文件。

本轮仅完成命令入口、本地校验、替身测试及真实本地候选的预览，没有操作 PLC。对应来源、哈希、命令与测试结果见[配套 JSON](35-cli-native-application-2026-10-19.json)。底层会话、USB 描述符与协议编解码逻辑不在本轮修改范围。

## 命令和明确作用

```bash
# 默认预览。报告26证实 STOP=raw0，RUN=raw1；ACK 不等于CPU状态读回。
.venv/bin/python tools/easy521_application_usb.py stop
.venv/bin/python tools/easy521_application_usb.py run

# 原读取语法继续可用；预览不会打开或检查此输出路径。
.venv/bin/python tools/easy521_application_usb.py \
  --remote-name stprog.o --credential-hex "$EASY521_CREDENTIAL_HEX" \
  --output-path /existing/private/new-backup.o

# 下载预览读取清单和文件。凭据必须由调用者显式提供8字节。
.venv/bin/python tools/easy521_application_usb.py download \
  --manifest /private/candidate/live-application-candidate.json \
  --credential-hex "$EASY521_CREDENTIAL_HEX" \
  --linux-device 001:002
```

`--execute` 才发起设备事务；执行时必须提供 `--linux-device BUS:ADDRESS`。`run/stop` 分别调用一次既有 `set_gcm_native(state=1/0)`，不自动读回或重试。其成功状态为 `control-acknowledged`，保持 `cpu_state_verified=false`。

`download --execute` 把已冻结的内存字节交给一次既有 `download_native` 调用：`GCM raw2 → 按清单顺序逐文件open2/write5 → 全部ACK后GCM raw3`。open2 会创建或截断相应远端文件，过程不自动 RUN、不重连重放、不回滚。`transfer-acknowledged` 只说明传输请求获得 ACK。协议依据和错误边界沿用[报告26](26-native-application-protocol-2026-10-19.md)。

发送/接收超时以毫秒传入，默认 2500 ms，允许 1–10000 ms；会话超时以秒传入，下载默认 120 s，读取和控制默认 30 s，上限均为 120 s。无自动重试。

## 下载清单的校验和来源边界

入口仅接受两个明确的候选格式，文件名、顺序、索引及数量必须完全匹配，不能借清单传入任意远端路径：

| 来源 | schema / profile | 文件数与归档策略 |
| --- | --- | --- |
| [报告28](28-fixed-application-config-2026-10-19.md) | `plcidf.easy521-fixed-application-candidate.v1` / `easy521-bess-existing-interface-v1` | 18；`project.hcpp` 必须是五字节 `Empty` 标记 |
| [报告30](30-live-application-config-2026-10-19.md) | `plcidf.easy521-live-application-inspection.v1` / `easy521-observed-live-interface-v1` | 17；明确省略来源归档，传输策略仍未解决 |

清单上限 1 MiB，单文件上限 8 MiB，清单与载荷的完整快照合计不超过 32 MiB。打开前检查常规文件，使用只读 `O_NOFOLLOW | O_NONBLOCK`，拒绝符号链接组件和 FIFO/目录；打开前后核对 inode、设备号、大小和纳秒时间。每个文件实际大小、SHA-256 必须匹配，随后重新读取清单及文件，核对字节和身份没有漂移。校验失败不调用底层写入 API，不访问设备。预览也执行这些本地校验。

`source_sha256` 取固定清单 `association["source.st"]` 或 live 清单 `source_association["source.st"]`。`stprog.o`、`stuserprog.o` 的实际字节哈希必须分别匹配对应关联字段。`build_sha256` 是**候选清单原始字节的 SHA-256**，报告明确标注 `build_sha256_kind="candidate-manifest"`，不会将它写成编译报告哈希。清单的关联声明不等于独立认证的编译证明；其他 `inputs` 来源记录原样保留，入口不宣称重新打开并认证了这些历史来源。

live 清单必须包含实际读回的 Easy521 `readback_target`，调用者指定的 Linux 位置若不同则拒绝。`001:002` 只是本次观察到的位置，不能证明永久设备身份；报告保持 `permanent_target_identity="unknown"`。清单中 runtime、设备接受、符号元数据和归档策略等字段原样保存在 `candidate_metadata`。继承固定接口或设备读回的符号元数据，不代表已经为新源码重新生成或证明设备检查规则；成功传输不会把 `downloadable=false` 改为 true，也不虚构固件兼容结论。

## 中断和验证证据

低层返回的中断报告保留已发送请求、部分接收、资源释放和 `unknown`，命令退出码为 130。若非预期中断直接逃出写入 API，入口保留设备访问未知、PLC 可能改变和未知事务结果，不伪造“未访问设备”。在本地快照阶段中断则保持 `not_sent` 和 `device_access=false`。

应用 USB、协议、原生传输合跑 213 项通过，0 跳过；其中本轮新增 41 项覆盖命令映射、旧读取预览、两种候选、顺序/大小/哈希/关联拒绝、JSON 重复键、预算、链接/FIFO 与打开前替换、快照漂移、不可变载荷、位置不符及中断边界。设备通信用 libusb C ABI 替身和 mock；没有真机写操作。

此外用本机实际的 fixed18 与 live17 候选各运行一次最终 CLI 预览，读取 18/17 个文件并保留原候选的限制字段；`run/stop` 也各预览一次。四份报告均为 `status=preview`、`device_access=false`。私有原始证据保存在 `.plcidf/research-35-cli-native-application-20261019/*-frozen-preview.json`，早期报告保留，具体清单与报告哈希见配套 JSON。

本入口把现有低层协议变成可调用的 Linux 工作流步骤；完整应用的型号/版本适配、运行时接受及业务效果仍需分别记录编译、下装和真机运行证据。
