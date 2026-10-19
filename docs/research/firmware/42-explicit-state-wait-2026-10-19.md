# 42. 显式 RUN/STOP 状态等待（2026-10-19）

应用 CLI 的 `run/stop` 新增 `--wait-state`，在一次控制 ACK 后有界地读取 GCM，只有实际读到目标状态才报告确认。默认不带此选项时继续保持 ACK-only，不自动读回。实现为 [set_gcm_and_wait_native](../../../tools/easy521_application_usb.py)；来源、哈希和离线验证见[配套 JSON](42-explicit-state-wait-2026-10-19.json)。本轮没有操作设备或准备 USB，也没有增加错误清除、恢复或重复写命令。

```bash
# 零设备I/O预览；RUN默认等待窗口5s，STOP默认1s。
.venv/bin/python tools/easy521_application_usb.py run --wait-state
.venv/bin/python tools/easy521_application_usb.py stop --wait-state

# 执行时必须指定当次Linux位置。示例BUS:ADDRESS不能替代当次身份核对。
.venv/bin/python tools/easy521_application_usb.py run \
  --wait-state --wait-timeout-s 5 --linux-device 001:002 --execute
```

`--wait-timeout-s` 仅随 `--wait-state` 使用，范围为有限 0.1–10 秒；不允许用于 read/download 或 GCM raw2/raw3。等待窗口从**控制 ACK 后**开始，控制发送本身仍使用已有 `--timeout-ms` 和 `--session-timeout-s` 边界。

## 单次写入与独立读回

流程只调用一次 SetGCM：STOP=0，RUN=1。只有完整关联 ACK 才进入等待；写入失败则原样保留控制结果并停止。随后每次调用固定原生 `gcm-state` 只读接口，使用新的请求关联、描述符检查和单次读取租约。合法非目标状态允许下一次新观察，它不是重试控制写入；任意一次读取失败、中断、目标不符或解析无效立即停止为 `unknown`，不继续轮询、不重连重放、不清故障。

默认 RUN 最多 500 次，间隔 10ms；STOP 最多 10 次，间隔 100ms。依据是[报告41](41-fixed-error-diagnostics-2026-10-19.md)恢复的 AutoShop 单次 Set 后读 GCM 的调用链。默认 STOP 首次等 100ms，RUN 立即首次读取；显式选择最短 0.1 秒 STOP 窗口时立即读取一次，避免首个 100ms 等待耗尽整个窗口。自定义窗口也有按间隔计算的明确读取次数上限。

每个读操作使用同一绝对 monotonic deadline 的剩余预算。既有 fixed reader 的 GET_CONFIGURATION、OUT、共享截止时间的 IN 各有传输超时，因此单次 timeout 取配置值与剩余毫秒数三分之一的较小者，不能每轮重新获得完整 5 秒。剩余不足 3ms 不再发读请求。libusb 的枚举、打开和释放操作没有超时参数；返回晚于 deadline 会保留时间证据，迟到目标也不会提升为按时成功，不声称能硬中断这些主机资源调用。

## 状态与身份语义

报告保留完整 `control_result`，以及每个 `state_observations` 的目标、原始请求/响应、实际 timeout、Unix/monotonic 起止时间。写入目标与每次读取必须匹配新鲜 Linux BUS:ADDRESS、VID/PID、bcdDevice。位置与描述符关联不是永久身份认证，报告保持 `permanent_target_identity=unknown`；各操作分别获取租约，也不声称整个等待窗口排他或证明状态变化一定由此写入导致。

在窗口内读到目标时返回 `status=state-confirmed`、`cpu_state_verified=true`，并指向具体读回记录；`application_execution_verified` 与 `causal_transition_verified` 继续为 false。超时返回 `state-wait-timeout`，保留最后观察值；读失败返回 `state-read-failed` 和 `unknown`，保留控制 ACK 与失败读的完整证据。ACK、CPU 状态、应用执行和业务输出继续分别验收。

## 验证

应用 USB、应用 codec、原生 USB 回归合跑 **269 项通过，0 跳过**，其中新增 27 项等待场景覆盖一次写、合法非目标后新读、读失败即停止、身份/状态/关联错误、共享 deadline 及超时缩减、迟到目标、读中断、预览零设备/租约/nonce/sleep、参数边界和最短 STOP 窗口。四个实际本地 CLI 预览均通过，报告在 `.plcidf/research-42-explicit-state-wait-20261019/`。控制和读取均用 mock/替身；这不证明真机已经进入 RUN。
