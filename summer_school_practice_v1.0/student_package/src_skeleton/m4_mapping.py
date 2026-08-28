"""M4 语义互操作与大模型辅助映射 —— OpenSky/TeachingLink → 统一态势模型。

本模块实现数据链软件暑期学校 M4 实验所需的全部能力：
  * verify_candidate_mapping  对预生成/大模型候选逐条人工核验，
                               修正字段重命名、层次转换、比例因子与偏置恢复，
                               填写规则、单位转换、空值策略、证据与 verified
  * map_to_unified             使用人工核验后的规则，把单条来源记录
                               映射为统一态势模型 JSON 对象

核验依据：source_field_definitions.md、teaching_message_spec.md、
opensky_field_dictionary.csv、partner_field_dictionary.csv、unified_model.json。
候选不是答案；每条正式映射必须有字段定义或测试证据。
"""

from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------------------
# 1. 候选核验 → 正式映射表
# ---------------------------------------------------------------------------

def verify_candidate_mapping(
    candidate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """依据字段定义、单位、有效性和样例，形成人工核验后的正式映射。

    不直接照抄预生成候选；逐条核对语义、单位、类型、空值策略与证据，
    修正字段重命名、层次转换、比例因子与偏置恢复错误。
    """
    # 人工核验后的完整正式映射表（依据 source_field_definitions.md）
    verified: list[dict[str, Any]] = [
        # ---- track_id ----
        {
            "source_format": "OpenSky",
            "input_field": "target_id",
            "unified_field": "track_id",
            "rule": "直接转为六位小写十六进制字符串，保留前导0",
            "unit_conversion": "无（字符串）",
            "null_strategy": "必需字段，缺失则记录不可接受",
            "evidence": "source_field_definitions.md: track_id 行；opensky_field_dictionary.csv index0",
            "verified": True,
            "candidate_issue": "候选正确，确认保留前导0",
        },
        {
            "source_format": "OpenSky",
            "input_field": "latest_time",
            "unified_field": "timestamp",
            "rule": "直接映射 Unix 秒",
            "unit_conversion": "无（整数秒）",
            "null_strategy": "必须为正整数；缺失或<=0则time_valid=false",
            "evidence": "source_field_definitions.md: timestamp 行；current_situation.csv latest_time=1710000120",
            "verified": True,
            "candidate_issue": "候选正确，确认正整数检查",
        },
        # ---- timestamp (TeachingLink) ----
        {
            "source_format": "TeachingLink",
            "input_field": "timestamp",
            "unified_field": "timestamp",
            "rule": "直接映射消息头 timestamp（uint32）",
            "unit_conversion": "无（整数秒）",
            "null_strategy": "必需字段；缺失则记录不可接受",
            "evidence": "teaching_message_spec.md offset 8-11；partner_current_situation.csv timestamp=1710000120",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        # ---- quality.time_source ----
        {
            "source_format": "OpenSky",
            "input_field": "timestamp_source",
            "unified_field": "quality.time_source",
            "rule": "直接映射字符串",
            "unit_conversion": "无",
            "null_strategy": "默认 position_time",
            "evidence": "source_field_definitions.md: quality.time_source 行",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "status_flags.bit2",
            "unified_field": "quality.time_source",
            "rule": "bit2=0 → position_time；bit2=1 → last_contact_fallback",
            "unit_conversion": "无（布尔→枚举）",
            "null_strategy": "默认 position_time",
            "evidence": "teaching_message_spec.md: status_flags bit2=timestamp_fallback；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选错误：把 bit2 映射为 quality.time_valid，实际 bit2 是时间源回退标志，应映射到 quality.time_source",
        },
        # ---- identity.callsign ----
        {
            "source_format": "OpenSky",
            "input_field": "callsign",
            "unified_field": "identity.callsign",
            "rule": "去除首尾空格后直接映射",
            "unit_conversion": "无（字符串）",
            "null_strategy": "空或缺失时为 null",
            "evidence": "source_field_definitions.md: identity.callsign 行；opensky_field_dictionary.csv index1 需去除首尾空格",
            "verified": True,
            "candidate_issue": "候选未列出 OpenSky callsign，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "callsign+validity_flags.bit6",
            "unified_field": "identity.callsign",
            "rule": "bit6=1 时去除补0后映射；bit6=0 时为 null",
            "unit_conversion": "无（ASCII 字符串）",
            "null_strategy": "有效位为0时 null",
            "evidence": "teaching_message_spec.md: validity_flags bit6=callsign；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选正确但需补充有效性位检查",
        },
        # ---- position.lat ----
        {
            "source_format": "OpenSky",
            "input_field": "lat",
            "unified_field": "position.lat",
            "rule": "直接映射，单位度",
            "unit_conversion": "无（度）",
            "null_strategy": "缺失或越界时 null",
            "evidence": "source_field_definitions.md: position.lat 行；opensky_field_dictionary.csv index5",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "latitude_code+validity_flags.bit0",
            "unified_field": "position.lat",
            "rule": "bit0=1 时 code/(2^22-1)*180-90；bit0=0 时 null",
            "unit_conversion": "code × (180/4194303) − 90，单位度",
            "null_strategy": "有效位为0时 null",
            "evidence": "teaching_message_spec.md: 纬度 Q((lat+90)/180*(2^22-1))；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选错误：把 latitude_code 映射为 position.lon（经纬度颠倒），修正为 position.lat",
        },
        # ---- position.lon ----
        {
            "source_format": "OpenSky",
            "input_field": "lon",
            "unified_field": "position.lon",
            "rule": "直接映射，单位度",
            "unit_conversion": "无（度）",
            "null_strategy": "缺失或越界时 null",
            "evidence": "source_field_definitions.md: position.lon 行；opensky_field_dictionary.csv index6",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "longitude_code+validity_flags.bit1",
            "unified_field": "position.lon",
            "rule": "bit1=1 时 code/(2^22-1)*360-180；bit1=0 时 null",
            "unit_conversion": "code × (360/4194303) − 180，单位度",
            "null_strategy": "有效位为0时 null",
            "evidence": "teaching_message_spec.md: 经度 Q((lon+180)/360*(2^22-1))；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选错误：把 longitude_code 映射为 position.lat（经纬度颠倒），修正为 position.lon",
        },
        # ---- position.alt ----
        {
            "source_format": "OpenSky",
            "input_field": "altitude",
            "unified_field": "position.alt",
            "rule": "直接映射，单位米",
            "unit_conversion": "无（米）",
            "null_strategy": "缺失时 null",
            "evidence": "source_field_definitions.md: position.alt 行；opensky_field_dictionary.csv index7",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "altitude_code+validity_flags.bit2",
            "unified_field": "position.alt",
            "rule": "bit2=1 时 code-1000；bit2=0 时 null",
            "unit_conversion": "code − 1000，单位米（物理偏置 1000m）",
            "null_strategy": "有效位为0时 null",
            "evidence": "teaching_message_spec.md: 高度 Q(altitude_m+1000)；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选错误：规则写'code乘1米'，遗漏物理偏置 -1000，修正为 code-1000",
        },
        # ---- position.alt_type ----
        {
            "source_format": "OpenSky",
            "input_field": "altitude_type",
            "unified_field": "position.alt_type",
            "rule": "直接映射字符串 barometric/geometric",
            "unit_conversion": "无",
            "null_strategy": "高度无效时 unknown",
            "evidence": "source_field_definitions.md: position.alt_type 行",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "status_flags.bit1",
            "unified_field": "position.alt_type",
            "rule": "bit1=0 → barometric；bit1=1 → geometric；高度无效时 unknown",
            "unit_conversion": "无（布尔→枚举）",
            "null_strategy": "altitude_valid=false 时 unknown",
            "evidence": "teaching_message_spec.md: status_flags bit1=altitude_is_geometric；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        # ---- motion.speed ----
        {
            "source_format": "OpenSky",
            "input_field": "speed",
            "unified_field": "motion.speed",
            "rule": "直接映射，单位 m/s",
            "unit_conversion": "无（m/s）",
            "null_strategy": "缺失时 null",
            "evidence": "source_field_definitions.md: motion.speed 行；opensky_field_dictionary.csv index9",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "speed_code+validity_flags.bit3",
            "unified_field": "motion.speed",
            "rule": "bit3=1 时 code*0.1；bit3=0 时 null",
            "unit_conversion": "code × 0.1，单位 m/s",
            "null_strategy": "有效位为0时 null",
            "evidence": "teaching_message_spec.md: 地速 Q(speed_m_s/0.1)；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        # ---- motion.heading ----
        {
            "source_format": "OpenSky",
            "input_field": "heading",
            "unified_field": "motion.heading",
            "rule": "直接映射，单位度，0<=heading<360",
            "unit_conversion": "无（度）",
            "null_strategy": "缺失或越界时 null",
            "evidence": "source_field_definitions.md: motion.heading 行；opensky_field_dictionary.csv index10",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "heading_code+validity_flags.bit4",
            "unified_field": "motion.heading",
            "rule": "bit4=1 时 code*0.01 且 <360；bit4=0 时 null",
            "unit_conversion": "code × 0.01，单位度",
            "null_strategy": "有效位为0时 null",
            "evidence": "teaching_message_spec.md: 航向 Q(heading_deg/0.01)；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        # ---- motion.vertical_rate ----
        {
            "source_format": "OpenSky",
            "input_field": "vertical_rate",
            "unified_field": "motion.vertical_rate",
            "rule": "直接映射，单位 m/s",
            "unit_conversion": "无（m/s）",
            "null_strategy": "缺失时 null",
            "evidence": "source_field_definitions.md: motion.vertical_rate 行；opensky_field_dictionary.csv index11",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "vertical_rate_code+validity_flags.bit5",
            "unified_field": "motion.vertical_rate",
            "rule": "bit5=1 时 code*0.01-327.68；bit5=0 时 null",
            "unit_conversion": "code × 0.01 − 327.68，单位 m/s（物理偏置 327.68）",
            "null_strategy": "有效位为0时 null",
            "evidence": "teaching_message_spec.md: 垂直速度 Q((vr+327.68)/0.01)；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        # ---- status.on_ground ----
        {
            "source_format": "OpenSky",
            "input_field": "on_ground",
            "unified_field": "status.on_ground",
            "rule": "直接映射为布尔值",
            "unit_conversion": "无（布尔）",
            "null_strategy": "默认 false",
            "evidence": "source_field_definitions.md: status.on_ground 行；opensky_field_dictionary.csv index8",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "status_flags.bit0",
            "unified_field": "status.on_ground",
            "rule": "bit0=1 → true；bit0=0 → false",
            "unit_conversion": "无（布尔）",
            "null_strategy": "默认 false",
            "evidence": "teaching_message_spec.md: status_flags bit0=on_ground；source_field_definitions.md",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        # ---- quality.position_valid ----
        {
            "source_format": "OpenSky",
            "input_field": "lat_valid AND lon_valid",
            "unified_field": "quality.position_valid",
            "rule": "经纬度均非空且在合法范围 [-90,90]/[-180,180] 时为 true",
            "unit_conversion": "无",
            "null_strategy": "任一无效则 false",
            "evidence": "source_field_definitions.md: quality.position_valid 行",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "validity_flags.bit0 AND validity_flags.bit1",
            "unified_field": "quality.position_valid",
            "rule": "经纬有效位均为1且解码值在合法范围时为 true",
            "unit_conversion": "无",
            "null_strategy": "任一有效位为0则 false",
            "evidence": "source_field_definitions.md: quality.position_valid 行；teaching_message_spec.md",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        # ---- quality.time_valid ----
        {
            "source_format": "OpenSky",
            "input_field": "latest_time",
            "unified_field": "quality.time_valid",
            "rule": "latest_time 为正整数时 true",
            "unit_conversion": "无",
            "null_strategy": "缺失或<=0则 false",
            "evidence": "source_field_definitions.md: quality.time_valid 行",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "timestamp AND message_valid",
            "unified_field": "quality.time_valid",
            "rule": "timestamp 为正整数且帧通过接收判据时 true；时间回退不等于时间无效",
            "unit_conversion": "无",
            "null_strategy": "timestamp 缺失或帧无效则 false",
            "evidence": "source_field_definitions.md: quality.time_valid 行",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        # ---- quality.message_valid ----
        {
            "source_format": "OpenSky",
            "input_field": "结构校验结果",
            "unified_field": "quality.message_valid",
            "rule": "源记录结构校验通过时 true",
            "unit_conversion": "无",
            "null_strategy": "默认 true（OpenSky 当前态势已通过 M3）",
            "evidence": "source_field_definitions.md: quality.message_valid 行",
            "verified": True,
            "candidate_issue": "候选未列出，补充",
        },
        {
            "source_format": "TeachingLink",
            "input_field": "message_valid",
            "unified_field": "quality.message_valid",
            "rule": "完整帧接收判据（头字段、长度、校验和、保留位、标志一致性及必需字段）",
            "unit_conversion": "无",
            "null_strategy": "帧无效则 false",
            "evidence": "source_field_definitions.md: quality.message_valid 行；teaching_message_spec.md 接收判据",
            "verified": True,
            "candidate_issue": "候选正确，确认不得扩大为来源可信",
        },
    ]
    return verified


# ---------------------------------------------------------------------------
# 2. 单条记录 → 统一态势模型
# ---------------------------------------------------------------------------

def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def map_to_unified(record: dict[str, Any], source_format: str) -> dict[str, Any]:
    """使用人工核验后的规则生成统一态势消息。

    source_format: "OpenSky" 或 "TeachingLink"
    record: 对应来源的当前态势记录（dict）
    返回：符合 unified_model.json 结构的 dict
    """
    if source_format == "OpenSky":
        return _map_opensky(record)
    if source_format == "TeachingLink":
        return _map_teachinglink(record)
    raise ValueError(f"未知来源格式: {source_format}")


def _map_opensky(r: dict[str, Any]) -> dict[str, Any]:
    """OpenSky 当前态势 → 统一模型。"""
    # track_id：六位小写十六进制，保留前导0
    track_id = str(r.get("target_id", "")).lower().strip()
    # timestamp：正整数
    ts_raw = r.get("timestamp")
    try:
        ts = int(ts_raw) if ts_raw not in (None, "") else None
    except (ValueError, TypeError):
        ts = None
    time_valid = ts is not None and ts > 0

    # callsign
    callsign = r.get("callsign")
    if callsign is not None:
        callsign = str(callsign).strip()
        if callsign == "":
            callsign = None

    # 位置
    lat = _to_float(r.get("latitude"))
    lon = _to_float(r.get("longitude"))
    alt = _to_float(r.get("altitude"))
    lat_valid = _to_bool(r.get("lat_valid"))
    lon_valid = _to_bool(r.get("lon_valid"))
    alt_valid = _to_bool(r.get("altitude_valid"))

    position_lat = lat if lat_valid and lat is not None else None
    position_lon = lon if lon_valid and lon is not None else None
    position_alt = alt if alt_valid and alt is not None else None

    # alt_type: OpenSky altitude_type 列；为空时若高度有效则默认 barometric
    raw_alt_type = r.get("altitude_type")
    if not alt_valid:
        alt_type = "unknown"
    elif raw_alt_type:
        alt_type = raw_alt_type
    else:
        # OpenSky baro_altitude 为优先高度源，缺失时默认 barometric
        alt_type = "barometric"

    # 运动
    speed = _to_float(r.get("speed")) if _to_bool(r.get("speed_valid")) else None
    heading = _to_float(r.get("heading")) if _to_bool(r.get("heading_valid")) else None
    vrate = _to_float(r.get("vertical_rate")) if _to_bool(r.get("vertical_rate_valid")) else None

    # 状态
    on_ground = _to_bool(r.get("on_ground"))

    # 质量
    position_valid = (position_lat is not None and position_lon is not None
                      and -90 <= position_lat <= 90
                      and -180 <= position_lon <= 180)
    # OpenSky timestamp_source 值为 time_position/last_contact，
    # 统一模型使用 position_time/last_contact_fallback
    raw_ts_src = r.get("timestamp_source")
    if raw_ts_src == "time_position":
        time_source = "position_time"
    elif raw_ts_src == "last_contact":
        time_source = "last_contact_fallback"
    else:
        time_source = raw_ts_src or "position_time"
    message_valid = True  # OpenSky 当前态势已通过 M3

    return {
        "track_id": track_id,
        "source": "OpenSky",
        "timestamp": ts if ts is not None else 0,
        "identity": {"callsign": callsign},
        "position": {
            "lat": position_lat,
            "lon": position_lon,
            "alt": position_alt,
            "alt_type": alt_type,
        },
        "motion": {
            "speed": speed,
            "heading": heading,
            "vertical_rate": vrate,
        },
        "status": {"on_ground": on_ground},
        "quality": {
            "position_valid": position_valid,
            "time_valid": time_valid,
            "message_valid": message_valid,
            "time_source": time_source,
            "anomaly_flags": [],
        },
    }


def _map_teachinglink(r: dict[str, Any]) -> dict[str, Any]:
    """TeachingLink 当前态势 → 统一模型。"""
    # track_id
    track_id = str(r.get("target_id", "")).lower().strip()

    # timestamp
    ts_raw = r.get("timestamp") or r.get("latest_time")
    try:
        ts = int(ts_raw) if ts_raw not in (None, "") else None
    except (ValueError, TypeError):
        ts = None
    time_valid = ts is not None and ts > 0

    # status_flags / validity_flags
    status_flags = _to_int(r.get("status_flags"))
    validity_flags = _to_int(r.get("validity_flags"))

    # callsign + bit6
    callsign_valid = bool(validity_flags & 0x40) if validity_flags is not None else _to_bool(r.get("callsign_valid"))
    callsign = r.get("callsign")
    if callsign is not None:
        callsign = str(callsign).strip()
    if not callsign_valid:
        callsign = None

    # 位置（按 validity_flags 恢复 null）
    lat_valid = bool(validity_flags & 0x01) if validity_flags is not None else _to_bool(r.get("lat_valid"))
    lon_valid = bool(validity_flags & 0x02) if validity_flags is not None else _to_bool(r.get("lon_valid"))
    alt_valid = bool(validity_flags & 0x04) if validity_flags is not None else _to_bool(r.get("altitude_valid"))

    lat = _to_float(r.get("latitude")) if lat_valid else None
    lon = _to_float(r.get("longitude")) if lon_valid else None
    alt = _to_float(r.get("altitude")) if alt_valid else None

    # alt_type: status_flags.bit1
    if status_flags is not None:
        alt_type = "geometric" if (status_flags & 0x02) else "barometric"
    else:
        alt_type = r.get("alt_type") or r.get("altitude_type") or "unknown"
    if not alt_valid:
        alt_type = "unknown"

    # 运动
    speed_valid = bool(validity_flags & 0x08) if validity_flags is not None else _to_bool(r.get("speed_valid"))
    heading_valid = bool(validity_flags & 0x10) if validity_flags is not None else _to_bool(r.get("heading_valid"))
    vrate_valid = bool(validity_flags & 0x20) if validity_flags is not None else _to_bool(r.get("vertical_rate_valid"))

    speed = _to_float(r.get("speed")) if speed_valid else None
    heading = _to_float(r.get("heading")) if heading_valid else None
    vrate = _to_float(r.get("vertical_rate")) if vrate_valid else None

    # on_ground: status_flags.bit0
    if status_flags is not None:
        on_ground = bool(status_flags & 0x01)
    else:
        on_ground = _to_bool(r.get("on_ground"))

    # time_source: status_flags.bit2
    if status_flags is not None:
        time_source = "last_contact_fallback" if (status_flags & 0x04) else "position_time"
    else:
        time_source = r.get("time_source") or "position_time"

    # 质量
    position_valid = (lat is not None and lon is not None
                      and -90 <= lat <= 90 and -180 <= lon <= 180)
    message_valid = _to_bool(r.get("message_valid"))

    return {
        "track_id": track_id,
        "source": "TeachingLink",
        "timestamp": ts if ts is not None else 0,
        "identity": {"callsign": callsign},
        "position": {
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "alt_type": alt_type,
        },
        "motion": {
            "speed": speed,
            "heading": heading,
            "vertical_rate": vrate,
        },
        "status": {"on_ground": on_ground},
        "quality": {
            "position_valid": position_valid,
            "time_valid": time_valid,
            "message_valid": message_valid,
            "time_source": time_source,
            "anomaly_flags": [],
        },
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None
