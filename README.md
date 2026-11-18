# 北岸水站单次取样归档

白班标定员登记取水渠探头的通道修订，值班员据此开单并逐条回放本地录制。设备为每张单最多触发一次取样，由测量和撤回在设备侧的实际先后确定终态。夜班重开同一工作区，续接未完取样并查阅固定回执。

## 运行

在项目根目录使用 Python 3.11。工程仅依赖标准库。

```text
python3 -B check_project.py
python3 -B demo.py
```

`check_project.py` 是项目物化时提供的完整回归入口，执行 `tests` 下的 `test_*.py` 套件。可直接用 `python3 -B -m unittest discover -s tests -t .` 运行同一套测试。演示使用项目内的 `recordings/handover.json` 和临时工作区，展示 A 实测 17、B 复用槽后实测 23，以及 C 先撤回获得空样本回执。演示还另用 `recordings/basic.json` 的根前缀和 `handover_inputs.py` 的样例包展示乱序等待、丢回复、归档失败后重试及重开领取固定出处，分别输出一行 `POINTWISE_SCENARIO` 和 `POINTWISE_HANDOVER` JSON。演示核对独立手算、重开后的回执和设备结果后输出。

## 从 Python 回放

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from capture.session import open_recording_session
from catalog.calibration import Calibration
from catalog.channels import ChannelKey
from catalog.limits import QualityLimits
from catalog.revisions import ChannelRevision
from recorder.events import load_recording

recording = load_recording(Path('recordings/basic.json'))
with TemporaryDirectory() as workspace:
    with open_recording_session(workspace, recording) as station:
        configuration = station.add_channel_revision(ChannelRevision(
            ChannelKey('north-bank', 'K1'), 'R1', Calibration(2, 1), QualityLimits(0, 35),
        ))
        station.submit_order('A', configuration)
        station.advance_recording('A-start')
        station.advance_recording('A-read')
        final, = station.advance_recording(order_id='A')
        receipt = station.read_receipt(final.receipt_id)
        print(receipt.raw_value, receipt.micro_units, receipt.quality)
```

示例结果为 `17 35 in_range`。每次实测将整数原值乘固定配置的整数比例，再加整数偏移，所得单位为微单位。质量在闭区间内为 `in_range`，其余为 `out_of_range`。原值和换算值可为零或负数。SQLite 使用可无损读回的整数文本保存读数、系数、限值和计算结果。撤回回执的原值、换算值、质量及测量出处为空。回执中的终态事件名和从 1 开始的位置保留实际设备出处。

## 会话接口

| 方法 | 行为 |
| --- | --- |
| `add_channel_revision(revision)` | 返回固定配置号。同站点、通道和修订的同内容可重投。非法标定、区间或不同内容的同键修订会拒绝。 |
| `submit_order(order_id, configuration_id)` | 返回当前订单状态。同号同配置可重投。未知配置或同号异配置拒绝，原单仍有效。 |
| `request_withdrawal(order_id)` | 保存意图并呈现 `pending_review`，已结案时返回原状态。未知单号报缺。 |
| `advance_recording(event_id)` | 推进录制中的下一个具名设备事件，并返回包含该订单状态的元组。错误事件、无已接纳单、未准备的撤回以及占槽冲突显式报告。 |
| `advance_recording(order_id=...)` | 核对一张单的设备事实、提交归档并续接可清理工作。省略单号时按单号顺序处理全部未完采集工作。返回状态元组。 |
| `read_order(order_id)` | 查询 `pending`、`pending_review`、`measured` 或 `withdrawn`，结案时附固定回执号。未知订单报缺。 |
| `read_receipt(receipt_id)` | 原样查询已提交快照。未知回执报缺。 |
| `submit_handover(package)` | 接纳、重投或拒绝一个交班包。返回 `accepted` 或 `replay`；非法输入、同号异内容、已知前序摘要不符以及对已绑定位置的竞争包报 `ValueError`。不推进设备。 |
| `read_receipt_source(receipt_id)` | 已提交回执的固定包出处 `{'package_id', 'sha256', 'package_position'}`；根录制终态为 `None`，未知回执报缺。只读。 |

模块级接口分别位于 `catalog/revisions.py`、`orders/intake.py`、`orders/withdrawal.py`、`capture/session.py`、`orders/status.py` 和 `archive/receipts.py`。交班包的解析、摘要、链接与出处存储在 `recorder/handover.py`。SQLite 错误向调用方报告。查询只读。在一个会话中依序调用；多个会话各自持有连接，录制位置由设备事务核对。

## 交班包

白班可以在设备已触发取样、尚未形成终态时结束。夜班把后续录制作为交班包送入同一工作区，`CaptureSession.submit_handover(package)` 只登记包身份，不推进位置或触发设备。包为恰含 `package_id`、`predecessor_id`、`predecessor_sha256` 和 `events` 的字典；摘要按 `HANDOVER_PACKAGES.md` 的规范 JSON 计算。首次接纳返回 `accepted`，同号同内容重投（含已消费或仍在等待的包）返回 `replay`，同号异内容或非法输入报 `ValueError` 且不留新包身份。前序未知时先持久等待；已知时摘要不符即拒绝；接纳后才发现摘要不符的包在推进时失去资格，重投仍为 `replay`。

`advance_recording(event_id)` 在根录制耗尽后按已选前缀的包链继续。只有已选前序的最后一条提交后，才考虑其唯一合格后继：后继须承诺前序身份与摘要，且事件号不得与已选前缀重复。无后继或有多个合格后继都报 `InvalidRecordingEvent`，位置与设备动作不变，按事件号调用也不能绕过竞争。首事件成功前竞争包仍可接纳；首事件的设备动作、位置和包绑定一同提交，因此首事件失败不绑定，成功后即使回复丢失，对已绑定位置新送的竞争包也被拒绝。重开继续用原根录制，无须重投已接纳包。

归档时，实际消费首终态的事件决定回执与出处。丢回复、继续推进或归档提交失败后重试，仍按原终态结算，并在同一次归档中固定包号、摘要和包内位置。`read_receipt_source` 只读已提交归档；根录制终态返回 `None`，旧回执保持原样，未知号报 `ReceiptNotFound`。处置约定见 `HANDOVER_NOTES.md`。

## 录制与重开

录制是具名事件的有序列表。`trigger` 提供非负槽号，`measure` 提供整数原值，`withdraw` 对已记录的撤回意图推进设备裁决。可以在事件上设置 `lose_reply`，设备先持久化动作和位置，随后模拟丢响应。具名事件和恢复信号分开，调用方可在设备形成终态与归档之间安排撤回、重开或另一项恢复动作。同一原发起关联始终指向同一次设备取样。

工作区包含 `business.sqlite3` 和 `device.sqlite3`。重开时使用同一份录制，并仅初始化缺失结构。再次执行样例登记时，同内容返回原配置号。人工修订、已有单据、回执、设备占槽及录制位置继续保留。传入不同录制时显式拒绝。

设备保留首个实际终态，直到按原取样身份清理。归档先核对原次身份，再原子提交固定回执和清理资格。恢复步骤可用 `fail_publish=True` 在新回执的 SQLite 提交处触发真实延迟约束失败。此时没有成功回执，设备仍可按原身份交出证据。恢复步骤可用 `lose_release_reply=True` 模拟设备已经释放但本地未收到确认。旧身份的后续确认只作用于该身份，复用原槽的新单继续保有自己的终态。回执提交后，即使清理未确认，仍可读取固定回执。
