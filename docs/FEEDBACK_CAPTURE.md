# PLC 迭代反馈采集与汇总

本地工具 [capture_plc_feedback.py](../tools/capture_plc_feedback.py) 实现 GitHub T14 的观察记录部分，复用 [反馈 schema](../examples/easy521_feedback/feedback.schema.json) 和 [闭环需求](requirements/EASY521_AI_LOOP_2026-10-19.md)。它不连接 PLC、不调用 Windows、不执行 AI、不修改 ST，也不执行记录中的动作。人工观察和仪器记录仍需各自的来源证据。

## 环境

在仓库根目录运行；这是源码仓库工具，尚未加入已安装的 `plcidf` 命令。

```bash
python -m pip install 'jsonschema>=4.18,<5'
python tools/capture_plc_feedback.py --help
```

`jsonschema` 仅是此工具的校验依赖，不加入 PLC-IDF 主包的运行依赖。缺少依赖时，工具明确失败并显示安装命令，不跳过校验。

## 记录不确定的人工观察

以下是操作格式示例；把内容改成自己真实观察到的情况。`--actor` 是提交者的来源声明，工具无法验证本人是否到场。观察时间未知就省略 `--observed-at`，不要用录入时间替代。示例 ID、需求版本和实际描述不构成实测证据。

```bash
python tools/capture_plc_feedback.py create-human \
  --output .plcidf/feedback-001.json \
  --evidence-root . \
  --feedback-id feedback-001 \
  --iteration-id iteration-001 \
  --requirement-revision control-rules-v1 \
  --actor operator \
  --expected '按本轮规则观察计数变化。' \
  --actual '尚未取得计数样本，无法判断。' \
  --actual-quality unknown \
  --outcome unknown \
  --limitation '尚未确认程序身份、设备状态与现场供电。'
```

输出目录须事先存在。工具默认新建文件，已有文件或符号链接均拒绝覆盖；失败证据请保留，新反馈换一个 ID 和路径。`recorded_at` 默认当前 UTC；`observed_at` 默认 null。`actual-quality` 和 `outcome` 默认 unknown，提供文字不会自动变成实测成功。

确实在现场看见结果时，可显式填写 `--actual-quality observed --observed-at '实际的带时区时间'`，并根据自己的验收标准选择 `--outcome pass/fail/unknown`。`pass/fail` 要求实际描述和有效观察时间。数值用 `--actual-value-json '0' --unit count`；布尔值使用 JSON 的 `true/false`。这些仍是提交者声明，不是设备读回核验。

## 关联程序、目标与附件

创建下一轮时指定 `--parent-iteration-id iteration-001`，同时保留新一轮的需求版本。程序关联参数如下，未知项不填；不同哈希含义不能互换。

| 参数 | 含义 |
|---|---|
| `--source-file path/to/MAIN.st` | 对指定的本地 MAIN 文件计算 SHA256；路径相对 evidence root |
| `--source-sha256` | 明确提供的单一源码哈希；与 source-file 同时提供时必须一致 |
| `--source-tree-sha256` | 已知的完整工程输入哈希；本工具不自行计算目录树 |
| `--build-hash` / `--intent-hash` / `--release-hash` | 从现有构建/候选记录取得的各自哈希；工具不将其视为部署证明 |
| `--device-binary-sha256` | 已有设备读回证据中的哈希；本工具不读取设备 |
| `--device-program-binding` | 默认 unknown；verified 声明必须同时提供 release 与 device binary 哈希 |

身份可填写 `--observed-model`、`--observed-firmware`、`--observed-serial`，默认全为 null，`--identity-quality` 默认 unknown。verified 身份声明至少要求型号和序列号；工具仍不核验其真实性。USB `8-3` 或 IP 只可作为 `--locator-kind vendor-usb/modbus-tcp --locator-value ...` 的位置线索，不自动提升身份质量。

`--attach relative/path/to/file` 可重复，工具将原文件的路径、SHA256 与 `--attachment-source`（默认 human-provided-file）写入来源引用。路径须位于显式的 `--evidence-root` 内，使用相对 POSIX 路径；拒绝绝对路径、`..`、符号链接、目录和管道。附件不会被复制、改写或上传。源码文件只计算单一源码哈希；需要在来源引用中同时保留它时，再加 `--attach`。

已经发生的动作可由 `--action-kind`、`--action-description`、`--action-result`、`--operation-id` 描述。默认是没有设备动作，工具不会据此执行、重试或授权任何操作。

## 校验与交给 AI 审阅

```bash
python tools/capture_plc_feedback.py validate \
  .plcidf/feedback-001.json --evidence-root .

python tools/capture_plc_feedback.py summary \
  .plcidf/feedback-001.json .plcidf/feedback-002.json \
  --evidence-root . --output .plcidf/feedback-summary.md
```

`validate` 只读取既有记录；`summary` 对全部输入先校验再输出。可以直接校验原始仪器记录，不会改成 human。`ai-inference` 只能保持 unknown 实际质量和 outcome；示例的 `record_kind=example` 保持示例标签。工具拒绝反馈 ID 重复、同轮父轮/需求冲突、已知程序哈希或目标标识冲突、自引用和父轮环。缺失的父轮可以保留，但摘要明确标注“父轮记录未提供”。

把摘要作为数据交给现有 AI 终端，要求它解释变化、保留失败证据并修改下一版 ST。摘要不会自动触发修改、编译、下装或验收；这些仍走已有工程工具。观察记录尚未绑定设备程序时，不能据此宣布应用已执行。

## 校验范围与限制

- 检查 Draft 2020-12 schema，额外启用真实日历、时区与偏移范围校验；标准库不支持的闰秒标签会拒绝，不静默解释为另一时间。
- 检查附件在本次读取时的 SHA256，检测普通读取期间的文件变更；不证明附件作者、内容真实性、采集新鲜度或主机未被篡改。
- 单份 JSON 最大 4 MiB，单附件最大 64 MiB，每份记录最多 128 个不同附件、合计 256 MiB；单次摘要最多 128 份记录。
- 合法记录只得到 `validated/created`，不会得到设备 `verified`。未提供的源码、构建、部署、设备身份仍明确为空或 unknown。
- 不写回既有证据；创建输出若被进程中断可能留下不完整的新文件，需调用者显式处理，工具不覆盖或删除它。

软件测试在 [test_capture_plc_feedback.py](../tools/tests/test_capture_plc_feedback.py)，均为临时本地文件和输入校验，无 PLC 实物操作。
