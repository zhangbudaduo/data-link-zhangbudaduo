"""M5 一致性保障 (Consistency Assurance).

按照 anomaly_rules.csv 中定义的固定规则识别缺失、延迟、重复和越界，
生成告警日志 (alert_log.csv) 和质量增强态势 (quality_situation.csv)。

固定规则:
    R1 POSITION_MISSING      lat 或 lon 为空                              HIGH
    R2 DATA_DELAYED          batch_time - record_time > 60 秒            MEDIUM
    R3 DUPLICATE_RECORD      target_id 和 timestamp 均相同                MEDIUM
    R4 HEADING_OUT_OF_RANGE  heading 非空且 (heading < 0 或 heading >= 360) MEDIUM

统一批次时间: batch_time = 1710000120
record_time 取 latest_time 或 timestamp。
heading 为空时不触发航向越界。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

BATCH_TIME = 1710000120

# 延迟阈值 (秒)
DELAY_THRESHOLD = 60

# 规则定义 (与 anomaly_rules.csv 保持一致)
RULES: list[dict[str, str]] = [
    {
        "rule_id": "R1",
        "alert_type": "POSITION_MISSING",
        "condition": "lat或lon为空",
        "severity": "HIGH",
    },
    {
        "rule_id": "R2",
        "alert_type": "DATA_DELAYED",
        "condition": "batch_time-record_time>60;record_time取latest_time或timestamp",
        "severity": "MEDIUM",
    },
    {
        "rule_id": "R3",
        "alert_type": "DUPLICATE_RECORD",
        "condition": "target_id和timestamp均相同",
        "severity": "MEDIUM",
    },
    {
        "rule_id": "R4",
        "alert_type": "HEADING_OUT_OF_RANGE",
        "condition": "heading非空且(heading<0或heading>=360)",
        "severity": "MEDIUM",
    },
]

# 严重等级优先级: HIGH > MEDIUM > NONE
SEVERITY_ORDER = {"HIGH": 3, "MEDIUM": 2, "NONE": 1}


def _is_empty(value: Any) -> bool:
    """判断字段是否为空。

    空字符串、None、纯空白均视为空。注意: 数值 0 不是空 (真实零值)。
    """
    if value is None:
        return True
    if isinstance(value, float):
        # NaN 视为空
        import math

        if math.isnan(value):
            return True
        return False
    if isinstance(value, (int, bool)):
        return False
    text = str(value).strip()
    return text == ""


def _to_float(value: Any) -> float | None:
    """安全转换为浮点数, 失败或空值返回 None。"""
    if _is_empty(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool:
    """解析 message_valid 字段, 容忍 True/False/1/0/字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y", "t"}


def _record_time(record: dict[str, Any]) -> int | None:
    """统一 record_time: 优先取 latest_time, 否则取 timestamp。"""
    for key in ("latest_time", "timestamp"):
        raw = record.get(key)
        if _is_empty(raw):
            continue
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            continue
    return None


def check_record(
    record: dict[str, Any], batch_time: int = BATCH_TIME
) -> list[dict[str, Any]]:
    """检查单条记录的位置缺失 (R1)、时间延迟 (R2) 和航向越界 (R4)。

    重复检查 (R3) 需要全量记录上下文, 由 ``check_duplicates`` 单独处理。
    本函数返回该记录触发的所有告警 (可能为多条)。
    """
    alerts: list[dict[str, Any]] = []
    target_id = str(record.get("target_id", "")).strip()
    timestamp = record.get("timestamp", "")
    alert_time = batch_time

    # ---- R1: POSITION_MISSING ----
    lat = record.get("lat")
    lon = record.get("lon")
    lat_missing = _is_empty(lat)
    lon_missing = _is_empty(lon)
    if lat_missing or lon_missing:
        missing_fields = []
        if lat_missing:
            missing_fields.append("lat")
        if lon_missing:
            missing_fields.append("lon")
        field = ",".join(missing_fields)
        alerts.append(
            {
                "alert_time": alert_time,
                "target_id": target_id,
                "alert_type": "POSITION_MISSING",
                "severity": "HIGH",
                "field": field,
                "description": f"位置字段缺失: {field} 为空",
            }
        )

    # ---- R2: DATA_DELAYED ----
    record_time = _record_time(record)
    if record_time is not None:
        delay = batch_time - record_time
        if delay > DELAY_THRESHOLD:
            alerts.append(
                {
                    "alert_time": alert_time,
                    "target_id": target_id,
                    "alert_type": "DATA_DELAYED",
                    "severity": "MEDIUM",
                    "field": "timestamp",
                    "description": (
                        f"数据延迟: batch_time({batch_time}) - "
                        f"record_time({record_time}) = {delay} > {DELAY_THRESHOLD}秒"
                    ),
                }
            )

    # ---- R4: HEADING_OUT_OF_RANGE ----
    heading_raw = record.get("heading")
    if not _is_empty(heading_raw):
        heading = _to_float(heading_raw)
        if heading is not None and (heading < 0 or heading >= 360):
            alerts.append(
                {
                    "alert_time": alert_time,
                    "target_id": target_id,
                    "alert_type": "HEADING_OUT_OF_RANGE",
                    "severity": "MEDIUM",
                    "field": "heading",
                    "description": (
                        f"航向越界: heading={heading}, 要求 0 <= heading < 360"
                    ),
                }
            )

    return alerts


def check_duplicates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """使用 target_id + timestamp 联合键检查重复 (R3)。

    同一 (target_id, timestamp) 出现多次时, 所有重复记录均产生告警
    (同一目标可以产生多个告警)。
    """
    alerts: list[dict[str, Any]] = []
    # 统计每个联合键出现的次数与索引
    key_to_indices: dict[tuple[str, str], list[int]] = {}
    for idx, record in enumerate(records):
        target_id = str(record.get("target_id", "")).strip()
        timestamp = str(record.get("timestamp", "")).strip()
        key = (target_id, timestamp)
        key_to_indices.setdefault(key, []).append(idx)

    for key, indices in key_to_indices.items():
        if len(indices) <= 1:
            continue
        target_id, timestamp = key
        for idx in indices:
            alerts.append(
                {
                    "alert_time": BATCH_TIME,
                    "target_id": target_id,
                    "alert_type": "DUPLICATE_RECORD",
                    "severity": "MEDIUM",
                    "field": "target_id,timestamp",
                    "description": (
                        f"重复记录: target_id={target_id}, "
                        f"timestamp={timestamp} 共出现 {len(indices)} 次"
                    ),
                    "_record_index": idx,
                }
            )
    return alerts


def build_quality_situation(
    records: list[dict[str, Any]], alerts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按 HIGH > MEDIUM > NONE 合成质量增强态势。

    每条原始记录生成一行, 字段含义:
        position_valid       lat 与 lon 均非空
        delayed               触发 R2
        duplicate_detected    触发 R3
        heading_valid         heading 为空 (不触发) 或 0 <= heading < 360
        message_valid         原始 message_valid 字段
        anomaly_level         该记录最高告警等级 (HIGH/MEDIUM/NONE)
        display_status        ERROR (有 HIGH) / WARNING (有 MEDIUM) / NORMAL
    """
    # 把告警按 (target_id, timestamp) 聚合, 便于回填到记录
    alert_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for alert in alerts:
        if alert.get("alert_type") == "DUPLICATE_RECORD":
            # 重复告警通过 _record_index 关联到具体记录
            continue
        key = (str(alert.get("target_id", "")).strip(), "")
        alert_map.setdefault(key, []).append(alert)

    # 重复告警按记录索引关联
    duplicate_indices: set[int] = set()
    for alert in alerts:
        if alert.get("alert_type") == "DUPLICATE_RECORD":
            idx = alert.get("_record_index")
            if idx is not None:
                duplicate_indices.add(idx)

    situations: list[dict[str, Any]] = []
    for idx, record in enumerate(records):
        target_id = str(record.get("target_id", "")).strip()
        timestamp = str(record.get("timestamp", "")).strip()

        # 字段级判定
        lat = record.get("lat")
        lon = record.get("lon")
        position_valid = not (_is_empty(lat) or _is_empty(lon))

        record_time = _record_time(record)
        delayed = (
            record_time is not None
            and (BATCH_TIME - record_time) > DELAY_THRESHOLD
        )

        duplicate_detected = idx in duplicate_indices

        heading_raw = record.get("heading")
        if _is_empty(heading_raw):
            heading_valid = True  # heading 为空时不触发该规则, 视为有效
        else:
            heading = _to_float(heading_raw)
            heading_valid = heading is not None and 0 <= heading < 360

        message_valid = _to_bool(record.get("message_valid"))

        # 收集该记录触发的告警, 计算最高等级
        record_alerts: list[dict[str, Any]] = []
        # 非重复告警: 按 target_id 匹配 (同一目标可有多条记录, 需进一步按 timestamp 过滤)
        for alert in alert_map.get((target_id, ""), []):
            # 对于 DATA_DELAYED, 告警的 timestamp 应与记录一致
            # 这里简单按 target_id 聚合, 因为 alert_time 与 batch_time 一致
            # 实际通过字段判定结果回填, 避免重复计算
            pass

        # 直接根据字段判定结果确定告警等级 (与 check_record 逻辑一致)
        levels: list[str] = []
        if not position_valid:
            levels.append("HIGH")
        if delayed:
            levels.append("MEDIUM")
        if duplicate_detected:
            levels.append("MEDIUM")
        if not heading_valid and not _is_empty(heading_raw):
            levels.append("MEDIUM")

        if "HIGH" in levels:
            anomaly_level = "HIGH"
            display_status = "ERROR"
        elif "MEDIUM" in levels:
            anomaly_level = "MEDIUM"
            display_status = "WARNING"
        else:
            anomaly_level = "NONE"
            display_status = "NORMAL"

        situations.append(
            {
                "target_id": target_id,
                "timestamp": timestamp,
                "position_valid": position_valid,
                "delayed": delayed,
                "duplicate_detected": duplicate_detected,
                "heading_valid": heading_valid,
                "message_valid": message_valid,
                "anomaly_level": anomaly_level,
                "display_status": display_status,
            }
        )

    return situations


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """读取 anomaly_cases.csv 为字典列表。"""
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)
    return records


def write_alert_log(alerts: list[dict[str, Any]], path: str | Path) -> None:
    """写出 alert_log.csv。"""
    fieldnames = ["alert_time", "target_id", "alert_type", "severity", "field", "description"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for alert in alerts:
            writer.writerow(alert)


def write_quality_situation(situations: list[dict[str, Any]], path: str | Path) -> None:
    """写出 quality_situation.csv。"""
    fieldnames = [
        "target_id",
        "timestamp",
        "position_valid",
        "delayed",
        "duplicate_detected",
        "heading_valid",
        "message_valid",
        "anomaly_level",
        "display_status",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for situation in situations:
            writer.writerow(situation)


def run(
    cases_path: str | Path,
    alert_log_path: str | Path,
    quality_situation_path: str | Path,
    batch_time: int = BATCH_TIME,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """完整执行 M5 一致性检查流程。

    步骤:
        1. 读取混合数据
        2. 对每条记录执行 R1/R2/R4 检查
        3. 对全量记录执行 R3 重复检查
        4. 合并告警并按规则顺序排序
        5. 合成质量态势
        6. 写出 alert_log.csv 与 quality_situation.csv
    """
    records = load_records(cases_path)

    # 1. 单记录检查 (R1, R2, R4)
    alerts: list[dict[str, Any]] = []
    for record in records:
        alerts.extend(check_record(record, batch_time=batch_time))

    # 2. 重复检查 (R3)
    duplicate_alerts = check_duplicates(records)
    alerts.extend(duplicate_alerts)

    # 3. 按规则顺序 + target_id 排序, 便于人工审阅
    rule_order = {"POSITION_MISSING": 0, "DATA_DELAYED": 1, "DUPLICATE_RECORD": 2, "HEADING_OUT_OF_RANGE": 3}
    alerts.sort(key=lambda a: (rule_order.get(a["alert_type"], 99), str(a.get("target_id", ""))))

    # 4. 合成质量态势
    situations = build_quality_situation(records, alerts)

    # 5. 写出文件
    write_alert_log(alerts, alert_log_path)
    write_quality_situation(situations, quality_situation_path)

    return alerts, situations


if __name__ == "__main__":
    import sys

    base = Path(__file__).resolve().parent
    cases_path = base / "anomaly_cases.csv"
    alert_log_path = base / "alert_log.csv"
    quality_situation_path = base / "quality_situation.csv"

    if len(sys.argv) >= 4:
        cases_path = Path(sys.argv[1])
        alert_log_path = Path(sys.argv[2])
        quality_situation_path = Path(sys.argv[3])

    alerts, situations = run(cases_path, alert_log_path, quality_situation_path)

    print(f"[M5] 读取记录: {len(situations)} 条")
    print(f"[M5] 生成告警: {len(alerts)} 条")
    print(f"[M5] alert_log.csv        -> {alert_log_path}")
    print(f"[M5] quality_situation.csv -> {quality_situation_path}")
    print()
    print("==== alert_log.csv ====")
    for a in alerts:
        print(
            f"  {a['alert_time']},{a['target_id']},{a['alert_type']},"
            f"{a['severity']},{a['field']}"
        )
    print()
    print("==== quality_situation.csv ====")
    for s in situations:
        print(
            f"  {s['target_id']},{s['timestamp']},pos={s['position_valid']},"
            f"delay={s['delayed']},dup={s['duplicate_detected']},"
            f"head={s['heading_valid']},level={s['anomaly_level']},"
            f"status={s['display_status']}"
        )
