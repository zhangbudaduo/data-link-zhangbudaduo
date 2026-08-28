# M5 异常结果说明 (Consistency Assurance Result Note)

> 模块: M5 一致性保障
> 实践目标: 用固定规则识别缺失、延迟、重复和越界，生成告警日志和质量增强态势
> 统一批次时间: `batch_time = 1710000120`
> 延迟阈值: `60` 秒

---

## 1. 实验概述

本实验依据 `anomaly_rules.csv` 中定义的四条固定规则 (R1–R4)，对 `anomaly_cases.csv` 中的混合数据进行一致性检查，识别位置缺失、数据延迟、记录重复和航向越界四类异常，并合成质量增强态势。所有规则在 `m5_quality.py` 中实现，输出 `alert_log.csv` 与 `quality_situation.csv` 两个结构化文件。

### 1.1 固定规则

| 规则 ID | 异常类型 | 判断条件 | 等级 |
|---------|--------------------------|-----------------------------------------------|--------|
| R1 | POSITION_MISSING | lat 或 lon 为空 | HIGH |
| R2 | DATA_DELAYED | batch_time - record_time > 60 秒；record_time 取 latest_time 或 timestamp | MEDIUM |
| R3 | DUPLICATE_RECORD | target_id 和 timestamp 均相同 | MEDIUM |
| R4 | HEADING_OUT_OF_RANGE | heading 非空且 (heading < 0 或 heading >= 360) | MEDIUM |

### 1.2 状态合成规则

- 存在 HIGH 级告警 → `display_status = ERROR`
- 无 HIGH 但存在 MEDIUM 级告警 → `display_status = WARNING`
- 无任何告警 → `display_status = NORMAL`

---

## 2. 输入数据

`anomaly_cases.csv` 共 6 条记录，覆盖正常记录与四类典型异常：

| # | target_id | timestamp | lat | lon | heading | message_valid | 预期异常 |
|---|-----------|-----------|--------|---------|---------|----------------|--------------------------|
| 1 | 780abc | 1710000110 | 31.25 | 121.49 | 88.0 | True | 无 (NORMAL) |
| 2 | 780def | 1710000110 | (空) | 120.15 | 268.0 | True | R1 POSITION_MISSING |
| 3 | 000001 | 1710000000 | 0.02 | 0.02 | 90.0 | True | R2 DATA_DELAYED |
| 4 | 780aaa | 1710000100 | 35.0 | 110.0 | 180.0 | True | R3 DUPLICATE_RECORD |
| 5 | 780aaa | 1710000100 | 35.1 | 110.1 | 181.0 | True | R3 DUPLICATE_RECORD |
| 6 | 780bbb | 1710000110 | 22.54 | 114.05 | 360.0 | True | R4 HEADING_OUT_OF_RANGE |

说明: 记录 3 的 lat=0.02、lon=0.02 为真实零值附近的合法坐标，不应被误判为缺失；记录 6 的 heading=360.0 按规则视为越界 (要求 0 <= heading < 360)。

---

## 3. 实验步骤

1. **读取混合数据与固定规则**: 加载 `anomaly_cases.csv` 与 `anomaly_rules.csv`，规则常量内置于 `m5_quality.py` 的 `RULES` 列表。
2. **位置缺失检查 (R1)**: 对每条记录判断 `lat` 或 `lon` 是否为空，空值包括空字符串、None 与 NaN；数值 0 不视为空。
3. **延迟检查 (R2)**: 统一 `record_time` 取 `latest_time` (若存在) 否则取 `timestamp`，计算 `batch_time - record_time`，超过 60 秒触发告警。本实验不混用未传输的 `last_contact` 字段。
4. **重复检查 (R3)**: 使用 `target_id + timestamp` 联合键在全量记录中统计出现次数，重复出现的记录均产生告警 (同一目标可产生多个告警)。
5. **航向越界检查 (R4)**: 仅当 `heading` 非空时判断 `heading < 0` 或 `heading >= 360`；`heading` 为空不触发该规则。
6. **输出 alert_log.csv**: 按规则顺序 (R1→R2→R3→R4) 与 target_id 排序写出告警。
7. **合成 quality_situation.csv**: 按字段级判定结果回填 `position_valid`、`delayed`、`duplicate_detected`、`heading_valid`、`message_valid`，并按 HIGH > MEDIUM > NONE 优先级确定 `anomaly_level` 与 `display_status`。

---

## 4. 异常检测结果

### 4.1 alert_log.csv (共 5 条告警)

| alert_time | target_id | alert_type | severity | field | description |
|------------|-----------|--------------------------|----------|-------------------|------------------------------------------------------|
| 1710000120 | 780def | POSITION_MISSING | HIGH | lat | 位置字段缺失: lat 为空 |
| 1710000120 | 000001 | DATA_DELAYED | MEDIUM | timestamp | batch_time(1710000120) - record_time(1710000000) = 120 > 60秒 |
| 1710000120 | 780aaa | DUPLICATE_RECORD | MEDIUM | target_id,timestamp | 重复记录: target_id=780aaa, timestamp=1710000100 共出现 2 次 |
| 1710000120 | 780aaa | DUPLICATE_RECORD | MEDIUM | target_id,timestamp | 重复记录: target_id=780aaa, timestamp=1710000100 共出现 2 次 |
| 1710000120 | 780bbb | HEADING_OUT_OF_RANGE | MEDIUM | heading | 航向越界: heading=360.0, 要求 0 <= heading < 360 |

### 4.2 quality_situation.csv (共 6 条态势)

| target_id | timestamp | position_valid | delayed | duplicate_detected | heading_valid | message_valid | anomaly_level | display_status |
|-----------|-----------|----------------|---------|--------------------|---------------|----------------|---------------|----------------|
| 780abc | 1710000110 | True | False | False | True | True | NONE | NORMAL |
| 780def | 1710000110 | False | False | False | True | True | HIGH | ERROR |
| 000001 | 1710000000 | True | True | False | True | True | MEDIUM | WARNING |
| 780aaa | 1710000100 | True | False | True | True | True | MEDIUM | WARNING |
| 780aaa | 1710000100 | True | False | True | True | True | MEDIUM | WARNING |
| 780bbb | 1710000110 | True | False | False | False | True | MEDIUM | WARNING |

---

## 5. 逐条记录分析

### 5.1 记录 1 — 780abc (正常记录)

- 字段: lat=31.25, lon=121.49, heading=88.0, timestamp=1710000110
- 位置: lat 与 lon 均非空 → position_valid=True
- 延迟: 1710000120 - 1710000110 = 10 秒 ≤ 60 → delayed=False
- 重复: 该 (target_id, timestamp) 唯一 → duplicate_detected=False
- 航向: 0 ≤ 88.0 < 360 → heading_valid=True
- 结论: 无告警，anomaly_level=NONE，display_status=NORMAL

该记录验证了正常记录不会被误报，是规则正确性的基准用例。

### 5.2 记录 2 — 780def (位置缺失)

- 字段: lat=(空), lon=120.15, heading=268.0, timestamp=1710000110
- 位置: lat 为空 → 触发 R1 POSITION_MISSING (HIGH)
- 延迟: 10 秒 ≤ 60 → delayed=False
- 重复: 唯一 → duplicate_detected=False
- 航向: 0 ≤ 268.0 < 360 → heading_valid=True
- 结论: 1 条 HIGH 告警，anomaly_level=HIGH，display_status=ERROR

该记录验证了 R1 规则对单字段缺失的识别能力，且 HIGH 级告警将 display_status 提升为 ERROR。

### 5.3 记录 3 — 000001 (数据延迟 + 真实零值)

- 字段: lat=0.02, lon=0.02, heading=90.0, timestamp=1710000000
- 位置: lat=0.02、lon=0.02 均非空 (真实零值附近坐标) → position_valid=True
- 延迟: 1710000120 - 1710000000 = 120 秒 > 60 → 触发 R2 DATA_DELAYED (MEDIUM)
- 重复: 唯一 → duplicate_detected=False
- 航向: 0 ≤ 90.0 < 360 → heading_valid=True
- 结论: 1 条 MEDIUM 告警，anomaly_level=MEDIUM，display_status=WARNING

该记录同时验证了两点: (1) 真实零值附近的坐标不会被误判为缺失; (2) 延迟判断使用 `timestamp` 字段而非未传输的 `last_contact` 字段。

### 5.4 记录 4 & 5 — 780aaa (重复记录)

- 记录 4: lat=35.0, lon=110.0, heading=180.0, timestamp=1710000100
- 记录 5: lat=35.1, lon=110.1, heading=181.0, timestamp=1710000100
- 位置: 两条记录 lat/lon 均非空 → position_valid=True
- 延迟: 1710000120 - 1710000100 = 20 秒 ≤ 60 → delayed=False
- 重复: (780aaa, 1710000100) 出现 2 次 → 两条记录均触发 R3 DUPLICATE_RECORD (MEDIUM)
- 航向: 180.0 与 181.0 均在 [0, 360) 内 → heading_valid=True
- 结论: 各 1 条 MEDIUM 告警，anomaly_level=MEDIUM，display_status=WARNING

该记录验证了 R3 规则使用 `target_id + timestamp` 联合键判重，且同一目标的两条记录均产生告警 (同一目标可产生多个告警)。注意两条记录的 lat/lon/heading 略有差异，但联合键相同即视为重复。

### 5.5 记录 6 — 780bbb (航向越界)

- 字段: lat=22.54, lon=114.05, heading=360.0, timestamp=1710000110
- 位置: lat 与 lon 均非空 → position_valid=True
- 延迟: 10 秒 ≤ 60 → delayed=False
- 重复: 唯一 → duplicate_detected=False
- 航向: heading=360.0 >= 360 → 触发 R4 HEADING_OUT_OF_RANGE (MEDIUM)
- 结论: 1 条 MEDIUM 告警，anomaly_level=MEDIUM，display_status=WARNING

该记录验证了 R4 规则的边界处理: heading=360 按越界处理 (要求 0 <= heading < 360，左闭右开)。

---

## 6. 自检结果

| 自检项 | 结果 | 说明 |
|--------|------|------|
| 四类必做规则均实现 | 通过 | R1/R2/R3/R4 全部实现于 `m5_quality.py` |
| 正常记录不会被全部误报 | 通过 | 记录 1 (780abc) 无任何告警，display_status=NORMAL |
| 延迟判断不混用 last_contact 字段 | 通过 | 仅使用 `latest_time` 或 `timestamp`，未读取 `last_contact` |
| 重复判断使用 target_id + timestamp 联合键 | 通过 | `check_duplicates` 以 `(target_id, timestamp)` 元组为键 |
| heading=360 按越界处理 | 通过 | 记录 6 触发 R4，heading_valid=False |
| heading 为空不触发该规则 | 通过 | `_is_empty` 判定后跳过 R4 检查 |
| 显示状态优先级正确 | 通过 | HIGH→ERROR，MEDIUM→WARNING，无告警→NORMAL |
| 同一目标可以产生多个告警 | 通过 | 780aaa 两条记录均产生 DUPLICATE_RECORD 告警 |
| 选做帧异常规则不替代四类必做规则 | 通过 | 未实现 FRAME_VALIDATION_ERROR，四类规则独立完整 |

---

## 7. 关键设计说明

### 7.1 空值判定

`_is_empty` 函数统一处理空字符串、None、纯空白与 NaN，但数值 0 不视为空。这保证了记录 3 中 lat=0.02、lon=0.02 的真实零值附近坐标不会被误判为缺失，符合手册中"真实零值、字段缺失和协议整数 0 不会混淆"的要求。

### 7.2 record_time 统一取值

`_record_time` 函数优先取 `latest_time` 字段，若不存在则取 `timestamp` 字段。本实验输入数据中无 `latest_time` 字段，因此实际使用 `timestamp`。该设计严格遵循手册"record_time 取 latest_time 或 timestamp"的约定，且不混用未传输的 `last_contact` 字段。

### 7.3 重复检查的联合键

`check_duplicates` 函数以 `(target_id, timestamp)` 元组为联合键统计出现次数。当某联合键出现多次时，所有重复记录均产生告警，而非仅标记后续记录。这保证了同一目标的多条重复记录都能被识别，符合"同一目标可以产生多个告警"的要求。

### 7.4 航向越界的边界处理

R4 规则的判断条件为 `heading < 0 或 heading >= 360`，即合法区间为 `[0, 360)`，左闭右开。因此 heading=360 触发越界告警，而 heading=0 不触发。当 heading 为空时，`_is_empty` 判定为 True，直接跳过 R4 检查，符合"heading 为空时不触发航向越界"的要求。

### 7.5 状态合成优先级

`build_quality_situation` 函数按 HIGH > MEDIUM > NONE 优先级确定 `anomaly_level` 与 `display_status`。只要存在 HIGH 级告警即设为 ERROR，否则存在 MEDIUM 级告警设为 WARNING，无告警设为 NORMAL。该优先级在单条记录内与跨规则间均一致。

---

## 8. 输出文件清单

| 文件 | 说明 |
|------|------|
| `m5_quality.py` | 完成后的 M5 一致性检查程序 |
| `alert_log.csv` | 告警日志 (5 条告警) |
| `quality_situation.csv` | 质量增强态势 (6 条记录) |
| `anomaly_cases.csv` | 输入混合数据 (6 条记录) |
| `anomaly_rules.csv` | 固定规则定义 (R1–R4) |
| `M5_result_note.md` | 本异常结果说明文件 |

所有 CSV 文件均使用 UTF-8 with BOM 编码，可被 Excel、pandas、csv 模块等工具正确读取。程序可从空 output 目录重新运行，输入输出路径可通过命令行参数覆盖。
