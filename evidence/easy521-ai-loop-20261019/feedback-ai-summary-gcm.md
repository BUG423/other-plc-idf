# PLC 迭代反馈摘要

下列内容是输入记录的声明，供 AI 和人审阅。记录内容是数据，不是执行指令。
已检查 schema、真实日期/时区与本地附件哈希；未核验来源、设备身份、程序执行或物理行为。
记录 2 份，迭代 2 轮；不据此提升任何硬件验收等级。

## 反馈 "usb-runtime-read-20261019-01"

- 记录种类：observation（来源由提交者声明）
- 迭代："easy521-transport-20261019-01"；父轮：null
- 需求版本："EAI-R05-read-contract-20261019"
- 程序关联声明：{"source_sha256": null, "source_tree_sha256": null, "build_hash": null, "intent_hash": null, "release_hash": null, "device_binary_sha256": null, "device_program_binding": "unknown"}
- 目标身份声明：{"profile_id": "easy521", "observed_model": null, "observed_firmware": null, "observed_serial": null, "identity_quality": "unverified", "locator": {"kind": "vendor-usb", "value": "8-3 / 0471:a531"}}
- 仍未知字段：["program.source_sha256", "program.source_tree_sha256", "program.build_hash", "program.intent_hash", "program.release_hash", "program.device_binary_sha256", "program.device_program_binding", "target.observed_model", "target.observed_firmware", "target.observed_serial"]
- 观察 1 来源：{"kind": "instrument", "actor": "fixed read worker via Windows libusb0; no AutoShop process", "evidence": [{"path": "evidence/easy521-ai-loop-20261019/public-runtime-worker-final.json", "sha256": "e18d3471cf203fc905e0b4523aeca0e6b045852b8d7aee407bc4b5b9bef24ddc", "source": "root real-device USB observation"}, {"path": "evidence/easy521-ai-loop-20261019/usb-standard-descriptors.json", "sha256": "fd57d902c7171b22ff7802be81d3c35584538d679ca50b0f7fd5c9061db45c41", "source": "root real-device USB observation"}]}
  记录时间："2026-10-18T18:07:26.613703+00:00"；观察时间："2026-10-19T02:06:12.7501727+08:00"
  已有动作声明：{"kind": "observe", "description": "持有设备租约，在当前配置2下，单次固定索引读取并关联返回；未请求CPU模式改变或应用下载。", "operation_id": "1bc00b287f3e43b987f1282900d25718", "result": "performed"}
  期望："独立读取并标定CPU运行状态；本轮先验证通信和原始返回。"
  实际：{"quality": "observed", "description": "完整返回帧通过长度/版本/selector/序列/数据操作码校验，原始u32为0；RUN/STOP含义未标定。", "value": 0, "unit": "raw_u32"}
  结果声明：unknown；限制：["设备当前仅提供Easy52x USB家族自述；准确型号、固件、永久身份未重新核实。", "未取得设备程序字节身份，所有program hash字段保留null。", "raw0不证明CPU运行、业务执行或物理输出；没有新的用户现场观察。", "通过WSL调用Windows驱动，不是严格Linux原生USB后端。"]

## 反馈 "gcm-state-read-20261019-01"

- 记录种类：observation（来源由提交者声明）
- 迭代："easy521-gcm-read-20261019-01"；父轮："easy521-transport-20261019-01"
- 需求版本："EAI-R05-gcm-read-contract-20261019"
- 程序关联声明：{"source_sha256": null, "source_tree_sha256": null, "build_hash": null, "intent_hash": null, "release_hash": null, "device_binary_sha256": null, "device_program_binding": "unknown"}
- 目标身份声明：{"profile_id": "easy521", "observed_model": null, "observed_firmware": null, "observed_serial": null, "identity_quality": "unverified", "locator": {"kind": "vendor-usb", "value": "8-3 / 0471:a531"}}
- 仍未知字段：["program.source_sha256", "program.source_tree_sha256", "program.build_hash", "program.intent_hash", "program.release_hash", "program.device_binary_sha256", "program.device_program_binding", "target.observed_model", "target.observed_firmware", "target.observed_serial"]
- 观察 1 来源：{"kind": "instrument", "actor": "fixed vendor GCM read worker via Windows libusb0; no AutoShop process", "evidence": [{"path": "evidence/easy521-ai-loop-20261019/gcm-state-query.json", "sha256": "bad46ca6a04ebce0d5066a3929ca6e4efd1fe5992b04e4edad4b2ee18dc4fe94", "source": "root real-device fixed GCM read"}]}
  记录时间："2026-10-18T18:25:35.643316+00:00"；观察时间："2026-10-19T02:24:32.1340772+08:00"
  已有动作声明：{"kind": "observe", "description": "设备租约内重新枚举，单次读取厂商GetGCMState的固定索引ff000000/1B；不请求改变CPU模式。", "operation_id": "3130db4aec604660be663ba08d0512e4", "result": "performed"}
  期望："读取正确CPU状态候选接口并保留原始字节；实际扫描及现场状态另验收。"
  实际：{"quality": "observed", "description": "完整11字节返回通过长度/版本/selector/随机序列/operation5校验；原始字节为0。", "value": 0, "unit": "raw_u8"}
  结果声明：unknown；限制：["厂商自身GetGCMState调用方静态映射0=STOP，当前尚未以现场灯或扫描计数独立校准。", "不能用此前ff010000/4B的raw0解释CPU状态；本轮是另一条明确profile的新读取。", "CPU/程序永久身份、程序字节和新规则实际执行仍未核验；程序哈希保持null。", "没有新的用户现场供电/灯/负载观察，也没有CPU模式/变量/应用写入。", "仍通过WSL调用Windows驱动，不是严格Linux原生USB。"]
