"""M2 协议解析与消息编解码 —— TeachingLink 位置状态教学帧。

本模块实现数据链软件暑期学校 M2 实验所需的全部编解码能力：
  * parse_state_vector      OpenSky 状态向量 -> 发送方内部结构化记录
  * calculate_checksum      前 39 字节教学校验和
  * encode_position_message 发送方内部记录 -> 41 字节大端帧
  * decode_position_message 41 字节帧 -> 接收方结构化记录（含接收判据）

TeachingLink 是学校自定义教学协议，不对应任何行业标准。
message_valid 仅代表帧通过本规范的格式与校验检查。
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

FRAME_SIZE = 41

MAGIC = 0x4453
VERSION = 1
MESSAGE_TYPE = 1
MESSAGE_LENGTH = 41

# status_flags 位定义
SF_ON_GROUND = 0x01            # bit0
SF_ALT_GEOMETRIC = 0x02         # bit1
SF_TIMESTAMP_FALLBACK = 0x04    # bit2
SF_RESERVED_MASK = 0xF8         # bit3-bit7 保留

# validity_flags 位定义
VF_LAT = 0x01   # bit0
VF_LON = 0x02   # bit1
VF_ALT = 0x04   # bit2
VF_SPEED = 0x08  # bit3
VF_HEADING = 0x10  # bit4
VF_VRATE = 0x20  # bit5
VF_CALLSIGN = 0x40  # bit6
VF_RESERVED_MASK = 0x80  # bit7 保留

# 22 位经纬度容器
LATLON_CODE_MAX = (1 << 22) - 1   # 4194303
LATLON_RESERVED_MASK = 0xC00000   # 3 字节(24位)容器最高 2 位 (bit22-bit23)

# 量程（闭区间，heading 为半开区间）
RANGE_LAT = (-90.0, 90.0)
RANGE_LON = (-180.0, 180.0)
RANGE_ALT = (-1000.0, 64535.0)       # code = alt + 1000, uint16
RANGE_SPEED = (0.0, 6553.5)          # code = speed / 0.1, uint16
RANGE_HEADING = (0.0, 360.0)         # 0 <= heading < 360
RANGE_VRATE = (-327.68, 327.67)      # code = (vr + 327.68) / 0.01, uint16

# 量化分辨率 / 偏置
ALT_OFFSET = 1000.0
VRATE_OFFSET = 327.68
SPEED_RES = 0.1
HEADING_RES = 0.01
VRATE_RES = 0.01

# 往返容差（一个量化单位）
TOL_LAT = 180.0 / LATLON_CODE_MAX
TOL_LON = 360.0 / LATLON_CODE_MAX
TOL_ALT = 1.0
TOL_SPEED = SPEED_RES
TOL_HEADING = HEADING_RES
TOL_VRATE = VRATE_RES

# OpenSky 状态向量字段索引
IDX_ICAO24 = 0
IDX_CALLSIGN = 1
IDX_ORIGIN_COUNTRY = 2
IDX_TIME_POSITION = 3
IDX_LAST_CONTACT = 4
IDX_LONGITUDE = 5
IDX_LATITUDE = 6
IDX_BARO_ALTITUDE = 7
IDX_ON_GROUND = 8
IDX_VELOCITY = 9
IDX_TRUE_TRACK = 10
IDX_VERTICAL_RATE = 11
IDX_GEO_ALTITUDE = 13
IDX_POSITION_SOURCE = 16


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _quantize(y: float) -> int:
    """统一量化函数 Q(y) = floor(y + 0.5)，y 为非负实数。"""
    return int(math.floor(y + 0.5))


def _in_range(value: float, lo: float, hi: float, inclusive_hi: bool = True) -> bool:
    if inclusive_hi:
        return lo <= value <= hi
    return lo <= value < hi


def _is_ascii_callsign(text: str) -> bool:
    """1-8 个可打印 ASCII 字符（0x20-0x7E）。"""
    if not text:
        return False
    if len(text) > 8:
        return False
    return all(0x20 <= ord(c) <= 0x7E for c in text)


def _hex_target_id(raw: Any) -> str | None:
    """规范化 icao24 -> 6 位小写十六进制字符串。"""
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if len(s) != 6:
        return None
    try:
        int(s, 16)
    except ValueError:
        return None
    return s


# ---------------------------------------------------------------------------
# 解析：OpenSky 状态向量 -> 发送方内部记录
# ---------------------------------------------------------------------------

def parse_state_vector(vector: list[Any]) -> dict[str, Any]:
    """将 OpenSky 状态向量转换为发送方内部结构化记录。

    返回的记录包含：必需字段、派生字段（timestamp_source / altitude / alt_type）、
    各可空字段的有效性，以及 errors 列表（元素为 (stage, field, problem_type,
    value, description)）。record_no 由调用方填充。
    """
    rec: dict[str, Any] = {
        "record_no": -1,
        "target_id": None,
        "callsign": None,
        "callsign_valid": False,
        "timestamp": None,
        "timestamp_source": None,
        "time_source": None,
        "timestamp_fallback": False,
        "lat": None,
        "lat_valid": False,
        "lon": None,
        "lon_valid": False,
        "altitude": None,
        "altitude_valid": False,
        "alt_type": "unknown",
        "altitude_is_geometric": False,
        "speed": None,
        "speed_valid": False,
        "heading": None,
        "heading_valid": False,
        "vertical_rate": None,
        "vertical_rate_valid": False,
        "on_ground": None,
        "errors": [],
    }

    def err(field: str, ptype: str, value: Any, desc: str) -> None:
        rec["errors"].append(("parse", field, ptype, value, desc))

    # --- target_id（必需） ---
    raw_icao = vector[IDX_ICAO24] if len(vector) > IDX_ICAO24 else None
    tid = _hex_target_id(raw_icao)
    if tid is None:
        err("target_id", "REQUIRED_FIELD_MISSING", raw_icao,
            "icao24 必需，须为 6 位十六进制字符串")
    else:
        rec["target_id"] = tid

    # --- on_ground（必需，布尔） ---
    raw_og = vector[IDX_ON_GROUND] if len(vector) > IDX_ON_GROUND else None
    if not isinstance(raw_og, bool):
        err("on_ground", "TYPE_ERROR", raw_og,
            "on_ground 必需且必须为布尔值")
    else:
        rec["on_ground"] = raw_og

    # --- timestamp（必需，优先 time_position，回退 last_contact） ---
    tp = vector[IDX_TIME_POSITION] if len(vector) > IDX_TIME_POSITION else None
    lc = vector[IDX_LAST_CONTACT] if len(vector) > IDX_LAST_CONTACT else None
    if tp is not None:
        rec["timestamp"] = int(tp)
        rec["timestamp_source"] = "time_position"
        rec["time_source"] = "position_time"
        rec["timestamp_fallback"] = False
    elif lc is not None:
        rec["timestamp"] = int(lc)
        rec["timestamp_source"] = "last_contact"
        rec["time_source"] = "last_contact_fallback"
        rec["timestamp_fallback"] = True
    else:
        err("timestamp", "REQUIRED_FIELD_MISSING", None,
            "time_position 与 last_contact 均为空，无法生成正常帧")

    # --- callsign（可空） ---
    raw_cs = vector[IDX_CALLSIGN] if len(vector) > IDX_CALLSIGN else None
    if raw_cs is None:
        rec["callsign"] = None
        rec["callsign_valid"] = False
    else:
        cs = str(raw_cs).strip()
        if cs == "":
            rec["callsign"] = None
            rec["callsign_valid"] = False
        elif _is_ascii_callsign(cs):
            rec["callsign"] = cs
            rec["callsign_valid"] = True
        else:
            if len(cs) > 8:
                err("callsign", "LENGTH_ERROR", cs,
                    f"callsign 去空格后 {len(cs)} 字符，超过 8 字节上限")
            else:
                err("callsign", "ENCODING_ERROR", cs,
                    "callsign 含非 ASCII 字符，无法编码")
            rec["callsign"] = None
            rec["callsign_valid"] = False

    # --- altitude（优先 baro，回退 geo） ---
    baro = vector[IDX_BARO_ALTITUDE] if len(vector) > IDX_BARO_ALTITUDE else None
    geo = vector[IDX_GEO_ALTITUDE] if len(vector) > IDX_GEO_ALTITUDE else None
    if baro is not None:
        rec["altitude"] = float(baro)
        rec["altitude_valid"] = True
        rec["alt_type"] = "barometric"
        rec["altitude_is_geometric"] = False
    elif geo is not None:
        rec["altitude"] = float(geo)
        rec["altitude_valid"] = True
        rec["alt_type"] = "geometric"
        rec["altitude_is_geometric"] = True
    else:
        rec["altitude"] = None
        rec["altitude_valid"] = False
        rec["alt_type"] = "unknown"
        rec["altitude_is_geometric"] = False

    # --- lat ---
    raw_lat = vector[IDX_LATITUDE] if len(vector) > IDX_LATITUDE else None
    if raw_lat is None:
        rec["lat"] = None
        rec["lat_valid"] = False
    else:
        lat = float(raw_lat)
        if _in_range(lat, *RANGE_LAT):
            rec["lat"] = lat
            rec["lat_valid"] = True
        else:
            err("latitude", "OUT_OF_RANGE", lat,
                f"纬度 {lat} 超出量程 {RANGE_LAT}")
            rec["lat"] = None
            rec["lat_valid"] = False

    # --- lon ---
    raw_lon = vector[IDX_LONGITUDE] if len(vector) > IDX_LONGITUDE else None
    if raw_lon is None:
        rec["lon"] = None
        rec["lon_valid"] = False
    else:
        lon = float(raw_lon)
        if _in_range(lon, *RANGE_LON):
            rec["lon"] = lon
            rec["lon_valid"] = True
        else:
            err("longitude", "OUT_OF_RANGE", lon,
                f"经度 {lon} 超出量程 {RANGE_LON}")
            rec["lon"] = None
            rec["lon_valid"] = False

    # --- speed ---
    raw_spd = vector[IDX_VELOCITY] if len(vector) > IDX_VELOCITY else None
    if raw_spd is None:
        rec["speed"] = None
        rec["speed_valid"] = False
    else:
        spd = float(raw_spd)
        if _in_range(spd, *RANGE_SPEED):
            rec["speed"] = spd
            rec["speed_valid"] = True
        else:
            err("velocity", "OUT_OF_RANGE", spd,
                f"地速 {spd} 超出量程 {RANGE_SPEED}")
            rec["speed"] = None
            rec["speed_valid"] = False

    # --- heading（半开区间 0 <= h < 360） ---
    raw_hd = vector[IDX_TRUE_TRACK] if len(vector) > IDX_TRUE_TRACK else None
    if raw_hd is None:
        rec["heading"] = None
        rec["heading_valid"] = False
    else:
        hd = float(raw_hd)
        if _in_range(hd, RANGE_HEADING[0], RANGE_HEADING[1], inclusive_hi=False):
            rec["heading"] = hd
            rec["heading_valid"] = True
        else:
            err("heading", "OUT_OF_RANGE", hd,
                f"航向 {hd} 超出量程 [0, 360)")
            rec["heading"] = None
            rec["heading_valid"] = False

    # --- vertical_rate ---
    raw_vr = vector[IDX_VERTICAL_RATE] if len(vector) > IDX_VERTICAL_RATE else None
    if raw_vr is None:
        rec["vertical_rate"] = None
        rec["vertical_rate_valid"] = False
    else:
        vr = float(raw_vr)
        if _in_range(vr, *RANGE_VRATE):
            rec["vertical_rate"] = vr
            rec["vertical_rate_valid"] = True
        else:
            err("vertical_rate", "OUT_OF_RANGE", vr,
                f"垂直速度 {vr} 超出量程 {RANGE_VRATE}")
            rec["vertical_rate"] = None
            rec["vertical_rate_valid"] = False

    return rec


# ---------------------------------------------------------------------------
# 校验和
# ---------------------------------------------------------------------------

def calculate_checksum(data_without_checksum: bytes) -> int:
    """计算前 39 字节无符号字节值之和模 65536。"""
    return sum(data_without_checksum) & 0xFFFF


# ---------------------------------------------------------------------------
# 编码：发送方内部记录 -> 41 字节帧
# ---------------------------------------------------------------------------

def encode_position_message(record: dict[str, Any], message_seq: int) -> bytes:
    """按 41 字节 TeachingLink 格式封装一条位置状态消息。

    调用前应确保必需字段（target_id / timestamp / on_ground）可用；
    可空字段通过 validity_flags 表达，无效时占位字节全为 0。
    """
    buf = bytearray(FRAME_SIZE)

    # 头部
    buf[0:2] = MAGIC.to_bytes(2, "big")
    buf[2] = VERSION
    buf[3] = MESSAGE_TYPE
    buf[4:6] = MESSAGE_LENGTH.to_bytes(2, "big")
    buf[6:8] = (int(message_seq) & 0xFFFF).to_bytes(2, "big")

    # timestamp
    ts = int(record["timestamp"])
    buf[8:12] = ts.to_bytes(4, "big")

    # target_id（uint24 / 3 字节，保留前导 0）
    tid_int = int(record["target_id"], 16)
    buf[12:15] = tid_int.to_bytes(3, "big")

    # callsign（ASCII / 8 字节，不足补 0）
    cs_bytes = bytearray(8)
    if record["callsign_valid"] and record["callsign"]:
        raw = record["callsign"].encode("ascii", errors="replace")
        cs_bytes[0:len(raw)] = raw[:8]
    buf[15:23] = cs_bytes

    # latitude_code（22 位 / 3 字节，最高 2 位保留为 0）
    if record["lat_valid"] and record["lat"] is not None:
        lat = record["lat"]
        code = _quantize((lat + 90.0) / 180.0 * LATLON_CODE_MAX)
        code = max(0, min(LATLON_CODE_MAX, code))
    else:
        code = 0
    buf[23:26] = code.to_bytes(3, "big")

    # longitude_code
    if record["lon_valid"] and record["lon"] is not None:
        lon = record["lon"]
        code = _quantize((lon + 180.0) / 360.0 * LATLON_CODE_MAX)
        code = max(0, min(LATLON_CODE_MAX, code))
    else:
        code = 0
    buf[26:29] = code.to_bytes(3, "big")

    # altitude_code（uint16，偏置 1000 m）
    if record["altitude_valid"] and record["altitude"] is not None:
        code = _quantize(record["altitude"] + ALT_OFFSET)
        code = max(0, min(0xFFFF, code))
    else:
        code = 0
    buf[29:31] = code.to_bytes(2, "big")

    # speed_code（uint16，0.1 m/s）
    if record["speed_valid"] and record["speed"] is not None:
        code = _quantize(record["speed"] / SPEED_RES)
        code = max(0, min(0xFFFF, code))
    else:
        code = 0
    buf[31:33] = code.to_bytes(2, "big")

    # heading_code（uint16，0.01°）
    if record["heading_valid"] and record["heading"] is not None:
        code = _quantize(record["heading"] / HEADING_RES)
        code = max(0, min(0xFFFF, code))
    else:
        code = 0
    buf[33:35] = code.to_bytes(2, "big")

    # vertical_rate_code（uint16，偏置 327.68 m/s）
    if record["vertical_rate_valid"] and record["vertical_rate"] is not None:
        code = _quantize((record["vertical_rate"] + VRATE_OFFSET) / VRATE_RES)
        code = max(0, min(0xFFFF, code))
    else:
        code = 0
    buf[35:37] = code.to_bytes(2, "big")

    # status_flags
    sf = 0
    if record["on_ground"]:
        sf |= SF_ON_GROUND
    if record["altitude_is_geometric"]:
        sf |= SF_ALT_GEOMETRIC
    if record["timestamp_fallback"]:
        sf |= SF_TIMESTAMP_FALLBACK
    buf[37] = sf & 0xFF

    # validity_flags
    vf = 0
    if record["lat_valid"]:
        vf |= VF_LAT
    if record["lon_valid"]:
        vf |= VF_LON
    if record["altitude_valid"]:
        vf |= VF_ALT
    if record["speed_valid"]:
        vf |= VF_SPEED
    if record["heading_valid"]:
        vf |= VF_HEADING
    if record["vertical_rate_valid"]:
        vf |= VF_VRATE
    if record["callsign_valid"]:
        vf |= VF_CALLSIGN
    buf[38] = vf & 0xFF

    # checksum（前 39 字节之和模 65536）
    cksum = calculate_checksum(bytes(buf[0:39]))
    buf[39:41] = cksum.to_bytes(2, "big")

    return bytes(buf)


# ---------------------------------------------------------------------------
# 解码：41 字节帧 -> 接收方结构化记录
# ---------------------------------------------------------------------------

def decode_position_message(data: bytes) -> dict[str, Any]:
    """检查帧接收条件并恢复接收方结构化记录。

    依次检查：长度、magic、version、message_type、message_length、
    checksum、经纬度容器保留位、两个标志字节保留位、标志/占位一致性。
    非法帧记录错误但不会抛出异常；message_valid 汇总帧级判据。
    """
    out: dict[str, Any] = {
        "target_id": None,
        "callsign": None,
        "callsign_valid": False,
        "timestamp": None,
        "timestamp_source": None,
        "time_source": None,
        "lat": None,
        "lon": None,
        "altitude": None,
        "alt_type": "unknown",
        "speed": None,
        "heading": None,
        "vertical_rate": None,
        "on_ground": False,
        "message_seq": None,
        "status_flags": None,
        "validity_flags": None,
        "latitude_code": None,
        "longitude_code": None,
        "altitude_code": None,
        "speed_code": None,
        "heading_code": None,
        "vertical_rate_code": None,
        "lat_valid": False,
        "lon_valid": False,
        "altitude_valid": False,
        "speed_valid": False,
        "heading_valid": False,
        "vertical_rate_valid": False,
        "magic": None,
        "version": None,
        "message_type": None,
        "message_length": None,
        "checksum": None,
        "expected_checksum": None,
        "message_valid": False,
        "validation_errors": [],
        "errors": [],
    }

    def err(field: str, ptype: str, value: Any, desc: str) -> None:
        out["errors"].append(("decode", field, ptype, value, desc))
        out["validation_errors"].append(ptype)

    # --- 长度 ---
    if len(data) != FRAME_SIZE:
        err("frame", "LENGTH_ERROR", len(data),
            f"帧长度 {len(data)} 不等于 {FRAME_SIZE}")
        return out

    # --- 头字段 ---
    magic = int.from_bytes(data[0:2], "big")
    version = data[2]
    msg_type = data[3]
    msg_len = int.from_bytes(data[4:6], "big")
    out["magic"] = magic
    out["version"] = version
    out["message_type"] = msg_type
    out["message_length"] = msg_len

    if magic != MAGIC:
        err("magic", "MAGIC_ERROR", f"0x{magic:04x}",
            f"magic 应为 0x{MAGIC:04x}")
    if version != VERSION:
        err("version", "VERSION_ERROR", version,
            f"version 应为 {VERSION}")
    if msg_type != MESSAGE_TYPE:
        err("message_type", "MESSAGE_TYPE_ERROR", msg_type,
            f"message_type 应为 {MESSAGE_TYPE}")
    if msg_len != MESSAGE_LENGTH:
        err("message_length", "LENGTH_ERROR", msg_len,
            f"message_length 应为 {MESSAGE_LENGTH}")

    # --- 校验和 ---
    stored_cksum = int.from_bytes(data[39:41], "big")
    calc_cksum = calculate_checksum(data[0:39])
    out["checksum"] = stored_cksum
    out["expected_checksum"] = calc_cksum
    if stored_cksum != calc_cksum:
        err("checksum", "CHECKSUM_ERROR", stored_cksum,
            f"校验和不一致：存储 {stored_cksum}，期望 {calc_cksum}")

    # --- 提取字段（即使有错误也尽量提取，便于诊断） ---
    out["message_seq"] = int.from_bytes(data[6:8], "big")
    out["timestamp"] = int.from_bytes(data[8:12], "big")
    tid_int = int.from_bytes(data[12:15], "big")
    out["target_id"] = f"{tid_int:06x}"

    out["status_flags"] = data[37]
    out["validity_flags"] = data[38]

    lat_code = int.from_bytes(data[23:26], "big")
    lon_code = int.from_bytes(data[26:29], "big")
    alt_code = int.from_bytes(data[29:31], "big")
    spd_code = int.from_bytes(data[31:33], "big")
    hd_code = int.from_bytes(data[33:35], "big")
    vr_code = int.from_bytes(data[35:37], "big")

    out["latitude_code"] = lat_code
    out["longitude_code"] = lon_code
    out["altitude_code"] = alt_code
    out["speed_code"] = spd_code
    out["heading_code"] = hd_code
    out["vertical_rate_code"] = vr_code

    # --- 保留位检查 ---
    if lat_code & LATLON_RESERVED_MASK:
        err("latitude_code", "RESERVED_BITS_ERROR",
            f"0x{lat_code:06x}",
            "纬度容器最高 2 位保留位非 0")
    if lon_code & LATLON_RESERVED_MASK:
        err("longitude_code", "RESERVED_BITS_ERROR",
            f"0x{lon_code:06x}",
            "经度容器最高 2 位保留位非 0")
    if out["status_flags"] & SF_RESERVED_MASK:
        err("status_flags", "RESERVED_BITS_ERROR",
            f"0x{out['status_flags']:02x}",
            "status_flags bit3-bit7 保留位非 0")
    if out["validity_flags"] & VF_RESERVED_MASK:
        err("validity_flags", "RESERVED_BITS_ERROR",
            f"0x{out['validity_flags']:02x}",
            "validity_flags bit7 保留位非 0")

    # --- 标志位解释 ---
    sf = out["status_flags"]
    vf = out["validity_flags"]
    out["on_ground"] = bool(sf & SF_ON_GROUND)
    out["altitude_is_geometric"] = bool(sf & SF_ALT_GEOMETRIC)
    out["timestamp_fallback"] = bool(sf & SF_TIMESTAMP_FALLBACK)
    out["time_source"] = "last_contact_fallback" if out["timestamp_fallback"] else "position_time"
    out["timestamp_source"] = "last_contact" if out["timestamp_fallback"] else "time_position"
    out["alt_type"] = "geometric" if out["altitude_is_geometric"] else "barometric"

    out["lat_valid"] = bool(vf & VF_LAT)
    out["lon_valid"] = bool(vf & VF_LON)
    out["altitude_valid"] = bool(vf & VF_ALT)
    out["speed_valid"] = bool(vf & VF_SPEED)
    out["heading_valid"] = bool(vf & VF_HEADING)
    out["vertical_rate_valid"] = bool(vf & VF_VRATE)
    out["callsign_valid"] = bool(vf & VF_CALLSIGN)

    # --- 标志/占位一致性 ---
    if not out["lat_valid"] and lat_code != 0:
        err("latitude_code", "FLAG_VALUE_INCONSISTENCY", lat_code,
            "lat 有效位为 0 但占位非 0")
    if not out["lon_valid"] and lon_code != 0:
        err("longitude_code", "FLAG_VALUE_INCONSISTENCY", lon_code,
            "lon 有效位为 0 但占位非 0")
    if not out["altitude_valid"] and alt_code != 0:
        err("altitude_code", "FLAG_VALUE_INCONSISTENCY", alt_code,
            "altitude 有效位为 0 但占位非 0")
    if not out["speed_valid"] and spd_code != 0:
        err("speed_code", "FLAG_VALUE_INCONSISTENCY", spd_code,
            "speed 有效位为 0 但占位非 0")
    if not out["heading_valid"] and hd_code != 0:
        err("heading_code", "FLAG_VALUE_INCONSISTENCY", hd_code,
            "heading 有效位为 0 但占位非 0")
    if not out["vertical_rate_valid"] and vr_code != 0:
        err("vertical_rate_code", "FLAG_VALUE_INCONSISTENCY", vr_code,
            "vertical_rate 有效位为 0 但占位非 0")

    callsign_bytes = data[15:23]
    if not out["callsign_valid"]:
        if any(b != 0 for b in callsign_bytes):
            err("callsign", "FLAG_VALUE_INCONSISTENCY",
                callsign_bytes.hex(),
                "callsign 有效位为 0 但占位非 0")
    else:
        cs = callsign_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        out["callsign"] = cs

    # --- 物理量恢复（仅有效字段） ---
    if out["lat_valid"]:
        out["lat"] = lat_code / LATLON_CODE_MAX * 180.0 - 90.0
    if out["lon_valid"]:
        out["lon"] = lon_code / LATLON_CODE_MAX * 360.0 - 180.0
    if out["altitude_valid"]:
        out["altitude"] = alt_code - ALT_OFFSET
    if out["speed_valid"]:
        out["speed"] = spd_code * SPEED_RES
    if out["heading_valid"]:
        out["heading"] = hd_code * HEADING_RES
    if out["vertical_rate_valid"]:
        out["vertical_rate"] = vr_code * VRATE_RES - VRATE_OFFSET

    # --- message_valid：帧级判据汇总 ---
    frame_fatal = {
        "LENGTH_ERROR", "MAGIC_ERROR", "VERSION_ERROR",
        "MESSAGE_TYPE_ERROR", "CHECKSUM_ERROR", "RESERVED_BITS_ERROR",
        "FLAG_VALUE_INCONSISTENCY",
    }
    out["message_valid"] = not any(
        e[2] in frame_fatal for e in out["errors"]
    )

    return out


# ---------------------------------------------------------------------------
# 直接运行入口：raw_states.json -> parse -> encode -> decode 调试链路
# ---------------------------------------------------------------------------

def _run_pipeline(states_path: str) -> None:
    import json
    import os

    # 路径解析规则：
    #   1. 绝对路径 -> 直接使用
    #   2. 相对路径 -> 先按当前工作目录(CWD)解析；若不存在再回退到脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.isabs(states_path):
        resolved = states_path
    else:
        cwd_candidate = os.path.abspath(states_path)
        script_candidate = os.path.join(script_dir, states_path)
        if os.path.exists(cwd_candidate):
            resolved = cwd_candidate
        elif os.path.exists(script_candidate):
            resolved = script_candidate
        else:
            # 都不存在时默认按 CWD 解析，让 open 抛出明确的 FileNotFoundError
            resolved = cwd_candidate

    with open(resolved, encoding="utf-8") as f:
        payload = json.load(f)

    states = payload["states"]
    required = ("target_id", "timestamp", "on_ground")

    print(f"加载 {len(states)} 条 OpenSky 状态向量（{resolved}）")
    print("-" * 90)

    seq = 0
    for i, vec in enumerate(states):
        rec = parse_state_vector(vec)
        rec["record_no"] = i + 1

        missing = [f for f in required if rec.get(f) is None]
        if missing:
            print(f"#{i + 1:>2} target={rec['target_id']!r:>12}  跳过编码，缺少: {missing}")
            for e in rec["errors"]:
                print(f"      parse error: {e}")
            print("-" * 90)
            continue

        seq += 1
        frame = encode_position_message(rec, seq)
        dec = decode_position_message(frame)

        status = "OK" if dec["message_valid"] else "FAIL"
        print(f"#{i + 1:>2} target={rec['target_id']!r:>12} cs={rec['callsign']!r:>10} "
              f"seq={seq} len={len(frame)} valid={status}")
        print(f"     lat={dec['lat']} lon={dec['lon']} alt={dec['altitude']} "
              f"spd={dec['speed']} hd={dec['heading']} vr={dec['vertical_rate']}")
        print(f"     flags: status=0x{dec['status_flags']:02x} validity=0x{dec['validity_flags']:02x} "
              f"on_ground={dec['on_ground']} alt_type={dec['alt_type']}")
        if not dec["message_valid"]:
            for e in dec["errors"]:
                print(f"      decode error: {e}")
        print("-" * 90)


if __name__ == "__main__":
    import os
    import sys

    json_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("..", "data", "raw_states.json")
    _run_pipeline(json_path)
