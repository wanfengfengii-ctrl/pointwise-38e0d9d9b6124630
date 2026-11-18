# 北岸水站交班包

白班录制可以在设备已经触发取样、尚未形成终态时结束。夜班把后续录制送入同一工作区，继续原单，并从固定回执领取首终态的实际包出处。原录制、订单、校准、归档和清理规则见 `README.md`。

## 送包和身份

新增 `CaptureSession.submit_handover(package)`。`package` 是字典，恰含 `package_id`、`predecessor_id`、`predecessor_sha256` 和 `events`。包号和前序包号为可用 UTF-8 编码的非空白字符串，保留号 `ROOT` 仅代表传给 `open_recording_session` 的原录制，且包不能引用自身。摘要是 64 个小写十六进制字符。`events` 为非空有序列表，每项为 `recordings/basic.json` 所示的 V1 事件对象，字段沿用 `RecordingEvent`，包括可省略的 `lose_reply`。包内事件号须唯一。

摘要为下面这个数组的规范 JSON 字节的 SHA-256 小写十六进制值。

```text
[package_id, predecessor_id, predecessor_sha256, rows]
```

每个事件对应一行 `[event_id, kind, order_id, slot_hex, raw_hex, lose_reply]`。槽号和原值用 Python `hex` 的形式表示，如 `"0x3"`、`"-0x11"`，缺省或不适用时为 `null`，`lose_reply` 缺省为 `false`。用 `json.dumps(..., ensure_ascii=True, separators=(',', ':'))` 生成 ASCII 字节，保留事件顺序。根录制使用 `ROOT`、两个 `null` 和同样的事件行编码。发送方只提交前序摘要，本包摘要由规定内容确定。

首次接纳返回字符串 `accepted`，送包本身不推进位置或触发设备。同号同规范内容重投返回 `replay`，包括已消费或仍在等待的包。同号异内容或非法输入报 `ValueError`，原有来件和成果保持原样。前序未知时持久接纳等待。首次提交时前序已知且摘要不符则拒绝。已接纳后才发现摘要不符的包失去推进资格，后续原样重投仍是 `replay`。提交失败不得留下新包身份。

## 推进与归档交接

沿用 `advance_recording(event_id)`。只有已选前序的最后一条已经提交后，才可开始其唯一合格后继。合格后继须承诺该前序的身份与摘要，且包内事件号与已选前缀互异。前序缺失、前序属于未消费分支、摘要不符或事件号与前缀碰撞的来件均不能参加裁决。若无合格后继或存在多个合格后继，推进报 `InvalidRecordingEvent`，位置和设备动作不变。按事件号调用不能绕过竞争。

新包首事件成功前，合格竞争包可以接纳。绑定随首事件的设备动作和位置一同提交。首事件失败不绑定。成功后，即使设备回复丢失，对已绑定位置新送的竞争包也须拒绝，原包重投仍返回 `replay`。已开始的包继续按包内顺序执行，耗尽后才可接下一个。全局位置从根录制开始连续计数，保留 V1 的设备校验。错误事件不得提交设备动作、位置或新的包绑定。重开继续传原根录制，无须重投已接纳包。多个会话对同一工作区送包和推进时，按各操作实际提交的先后裁决。

沿用 `advance_recording(order_id=...)` 核定及结案。无论首终态是实测还是撤回，都以实际消费它的事件为准。丢回复或推进了更多事件之后仍须恢复该事件的实际包出处。原回执字段、全局事件位置、校准结果和清理规则保持兼容。归档失败时回执和出处均不可读，设备首终态仍可用于重试。归档成功时回执和出处一起固定。

新增 `CaptureSession.read_receipt_source(receipt_id)`。包内终态返回 `{'package_id': str, 'sha256': str, 'package_position': int}`，位置从 1 开始。根录制终态和原有回执返回 `None`，未知号抛原有 `archive.receipts.ReceiptNotFound`。查询只读取已提交的归档，不重新读取设备终态或根据当前录制位置推算。清理、推进、重开或重投不能改变已经签发的出处。

## 样例和交付核验

`handover_inputs.py` 提供 `ROOT_RECORDING`、`U2`、`U3`、`ALT`、`STALE` 和 `EXPECTED_A_SOURCE`。运行 `python3 -B handover_inputs.py` 可核对样例摘要和有效及失配的前序承诺。

- 按 `README.md` 开 A 单并推进根录制的 `A-start`。先送 `U3` 和 `STALE`，关闭后用原根录制重开。请求 `A-confirm` 须报错，设备位置仍为 1。接纳 U2 并原样重投，依次返回 `accepted`、`replay`。`A-read` 消费 U2 第 1 条并丢回复。此时提前请求 `A-confirm` 仍报错，位置为 2。推进 `A-repeat` 后才可推进 U3 的 `A-confirm`，最终位置为 4。STALE 没有接续资格。
- 在上述序列归档 A 时用原有 `fail_publish=True` 令提交失败。用 `receipt:A` 查回执及出处均报缺，A 的设备首终态仍在。重开后重试归档并清理，回执为原值 17、换算 35、`in_range`，全局终态为第 2 条 `A-read`，出处等于 `EXPECTED_A_SOURCE`。再重开读取仍一致，A 只有一次触发和一份回执。
- 在独立工作区接纳 `U2` 后，先错调 `A-repeat`，再接纳 `ALT`。错调未绑定 U2，两个合格来件使 `A-read` 报冲突，设备位置仍为 1，A 未形成终态。另开工作区，若先让 U2 的 `A-read` 成功提交再送 `ALT`，其提交必须报错，即使 U2 的设备回复已经丢失。

保留全部原回归。把联合业务核验接入现有 `.pointwise-runtime.json` 的 `verify`，失败须返回非零退出码。让 `demo` 实际展示从乱序等待到重开领取的回执和出处。附简短说明，交代录制消费何时生效及固定出处如何跨入回执归档。竞争分支改选、自动拉包和历史回收不在本次交付范围内。
