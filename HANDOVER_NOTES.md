# 交班包交接说明

本次在现有 Python/SQLite 工程上扩展交班送包、推进和回执出处。原接口、
原回归和根录制行为保持不变。

## 何时生效

- **送包**只登记包身份。`submit_handover` 在业务库写入包、事件行和规范摘要，
  不移动设备位置、不触发取样，也不改变订单状态。因此可以先送后到，乱序包
  在前序未知时持久等待，跨重开仍可重投。
- **推进**时才生效。`advance_recording(event_id)` 先在业务库按已选前缀裁决出
  唯一合格后继，再提交该事件：设备库把动作、全局位置和包绑定写进同一个
  设备事务（`device.sqlite3` 的 `recording_package_bindings`），业务库的进度、
  事件出处和包链镜像在同一个业务事务里提交。首事件失败或错误事件不会留下
  包绑定；回复丢失时设备与出处都已提交，只把进度标为待核对。若进程在设备
  提交之后、业务镜像之前中断，重开或下一次推进会用原根录制从设备绑定补齐
  业务镜像，并在缺少进度时按订单关联重建。
- 业务写事务在设备提交期间一直持有，因此多个会话的送包与推进按实际提交
  先后裁决；已绑定位置之后新到的竞争包被拒绝，原包重投仍是 `replay`。

## 固定出处如何跨入回执

- 每个已提交的包事件在 `event_sources` 里按全局位置记录
  `(package_id, sha256, package_position)`。
- 归档时由设备首终态事件的实际全局位置查回该出处，随回执一起在
  `receipt_sources` 提交；`fail_publish=True` 使回执和出处一起回滚，
  设备终态仍可用于重试。
- `read_receipt_source` 只读 `receipt_sources`，不重读设备、不按当前录制
  位置推算。清理、继续推进、重开或重投都不会改变已签发出处。根录制终态
  没有包出处，返回 `None`；旧回执保持原样。

## 文件

- `recorder/handover.py`：包校验、规范摘要、链接裁决、包链与事件出处存储。
- `recorder/bindings.py`：设备侧包绑定，随设备动作和位置一同提交。
- `recorder/endpoint.py`：新增按已解析事件推进，复用原设备校验。
- `capture/session.py`：`submit_handover`、`read_receipt_source`，以及把设备
  提交和业务绑定合并为一次一致提交。
- `capture/reconcile.py` / `capture/evidence.py` / `archive/publication.py`：
  出处随终态归档一起固定。
- `tests/test_handover.py`：送包、乱序等待、幂等、摘要失配、竞争、首事件
  绑定、丢回复、归档失败重试、重开领取及固定出处。
- `demo.py`：原有 `POINTWISE_SCENARIO` 之后新增 `POINTWISE_HANDOVER`，实际
  演示乱序续采、丢回复与重开领取。

## 运行核验

```text
python3 -B handover_inputs.py   # 样例摘要与前序承诺
python3 -B check_project.py     # 全部回归（含交班包）
python3 -B demo.py              # 两份场景 JSON
```

`.pointwise-runtime.json` 的 `verify`/`demo` 入口未改，仍分别执行
`check_project.py` 和 `demo.py`。

## 范围外

竞争分支改选、自动拉包和历史回收不在本次交付范围内。
