"""M3 单源多时刻关联与当前态势 —— TeachingLink 航迹管理。

本模块实现数据链软件暑期学校 M3 实验所需的全部能力：
  * decode_multitime_messages  批量解码多时间片消息（复用 M2 解码器）
  * group_and_sort_tracks      按 target_id 分组、按 timestamp 升序排序、
                               生成 track_sequence_no（同目标内从 1 开始）
  * build_current_situation    每个目标取时间最新的可接受记录
  * write_decoded_multitime    输出 decoded_multitime.csv
  * write_track_table          输出 track_table.csv
  * write_current_situation    输出 current_situation.csv

可接受记录判据：message_valid=true 且 target_id、timestamp 可用。
可选位置或运动字段为 None 时，记录仍可进入航迹和当前态势。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

# 复用 M2 协议模块（同目录）
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import m2_protocol as proto  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 批量解码
# ---------------------------------------------------------------------------

def decode_multitime_messages(bin_data: bytes) -> list[dict[str, Any]]:
    """批量解码多时间片消息。

    将二进制流按 41 字节切分，逐帧调用 M2 的 decode_position_message。
    每帧解码结果附带 frame_index（在流中的序号，从 0 开始）。
    非法帧不会导致整体崩溃，仍保留在返回列表中（message_valid=False）。
    """
    frames: list[dict[str, Any]] = []
    n = len(bin_data) // proto.FRAME_SIZE
    for i in range(n):
        chunk = bin_data[i * proto.FRAME_SIZE:(i + 1) * proto.FRAME_SIZE]
        dec = proto.decode_position_message(chunk)
        dec["frame_index"] = i
        frames.append(dec)
    return frames


# ---------------------------------------------------------------------------
# 2. 可接受记录判据
# ---------------------------------------------------------------------------

def is_acceptable(record: dict[str, Any]) -> bool:
    """判断记录是否可接受进入航迹。

    判据：message_valid=true 且 target_id、timestamp 可用（非 None）。
    可选位置或运动字段为 None 时，记录仍可接受。
    """
    if not record.get("message_valid", False):
        return False
    if record.get("target_id") is None:
        return False
    if record.get("timestamp") is None:
        return False
    return True


# ---------------------------------------------------------------------------
# 3. 分组、排序与 track_sequence_no
# ---------------------------------------------------------------------------

def group_and_sort_tracks(
    decoded_frames: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 target_id 分组、按 timestamp 升序排序，生成 track_sequence_no。

    返回:
      track_rows: 航迹表行（仅可接受记录），每行含 track_sequence_no
      rejected:   被拒绝的记录（不可接受），用于日志
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for dec in decoded_frames:
        if is_acceptable(dec):
            accepted.append(dec)
        else:
            rejected.append(dec)

    # 按 target_id 分组
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in accepted:
        tid = rec["target_id"]
        groups.setdefault(tid, []).append(rec)

    # 每组内按 timestamp 升序排序，生成 track_sequence_no（从 1 开始）
    track_rows: list[dict[str, Any]] = []
    for tid in sorted(groups.keys()):
        group = sorted(groups[tid], key=lambda r: r["timestamp"])
        for seq_no, rec in enumerate(group, start=1):
            row = dict(rec)  # 浅拷贝
            row["track_sequence_no"] = seq_no
            track_rows.append(row)

    return track_rows, rejected


# ---------------------------------------------------------------------------
# 4. 当前态势
# ---------------------------------------------------------------------------

def build_current_situation(
    track_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """每个目标取时间最新的可接受记录作为当前态势。

    track_rows 已按 target_id 分组、组内按 timestamp 升序排序，
    因此每组的最后一行即为时间最新的记录。
    """
    current: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in track_rows:
        groups.setdefault(row["target_id"], []).append(row)

    for tid in sorted(groups.keys()):
        group = groups[tid]
        # 组内已按 timestamp 升序，最后一行即最新
        latest = dict(group[-1])
        latest["is_current"] = True
        current.append(latest)

    return current


# ---------------------------------------------------------------------------
# 5. CSV 输出
# ---------------------------------------------------------------------------

# decoded_multitime.csv 列：解码全字段
DECODED_COLUMNS = [
    "frame_index",
    "message_seq",
    "target_id",
    "timestamp",
    "timestamp_source",
    "on_ground",
    "altitude_type",
    "status_flags",
    "validity_flags",
    "latitude",
    "longitude",
    "altitude",
    "speed",
    "heading",
    "vertical_rate",
    "callsign",
    "lat_valid",
    "lon_valid",
    "altitude_valid",
    "speed_valid",
    "heading_valid",
    "vertical_rate_valid",
    "callsign_valid",
    "checksum",
    "expected_checksum",
    "message_valid",
    "validation_errors",
]

# track_table.csv 列：航迹表
TRACK_COLUMNS = [
    "target_id",
    "track_sequence_no",
    "timestamp",
    "timestamp_source",
    "on_ground",
    "altitude_type",
    "latitude",
    "longitude",
    "altitude",
    "speed",
    "heading",
    "vertical_rate",
    "callsign",
    "lat_valid",
    "lon_valid",
    "altitude_valid",
    "speed_valid",
    "heading_valid",
    "vertical_rate_valid",
    "callsign_valid",
    "message_seq",
    "frame_index",
]

# current_situation.csv 列：当前态势
CURRENT_COLUMNS = [
    "target_id",
    "timestamp",
    "timestamp_source",
    "on_ground",
    "altitude_type",
    "latitude",
    "longitude",
    "altitude",
    "speed",
    "heading",
    "vertical_rate",
    "callsign",
    "lat_valid",
    "lon_valid",
    "altitude_valid",
    "speed_valid",
    "heading_valid",
    "vertical_rate_valid",
    "callsign_valid",
    "message_seq",
    "track_sequence_no",
    "frame_index",
]


def _fmt(v: Any) -> str:
    """格式化单元格值。None -> 空字符串。"""
    if v is None:
        return ""
    if isinstance(v, float):
        # 保留足够精度
        if v == int(v):
            return f"{v:.1f}"
        return f"{v:.6f}".rstrip("0").rstrip(".")
    if isinstance(v, list):
        return ";".join(str(x) for x in v)
    return str(v)


def write_decoded_multitime(
    decoded_frames: list[dict[str, Any]],
    path: Path,
) -> None:
    """输出 decoded_multitime.csv。"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(DECODED_COLUMNS)
        for dec in decoded_frames:
            w.writerow([_fmt(dec.get(c)) for c in DECODED_COLUMNS])


def write_track_table(
    track_rows: list[dict[str, Any]],
    path: Path,
) -> None:
    """输出 track_table.csv。"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(TRACK_COLUMNS)
        for row in track_rows:
            w.writerow([_fmt(row.get(c)) for c in TRACK_COLUMNS])


def write_current_situation(
    current_rows: list[dict[str, Any]],
    path: Path,
) -> None:
    """输出 current_situation.csv。"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CURRENT_COLUMNS)
        for row in current_rows:
            w.writerow([_fmt(row.get(c)) for c in CURRENT_COLUMNS])


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="M3 单源多时刻关联与当前态势")
    ap.add_argument(
        "--input",
        default="/home/z/my-project/upload/6a8eb32741e8b2607a23d0c4_partner_messages_multitime.bin",
        help="输入二进制消息流",
    )
    ap.add_argument(
        "--outdir",
        default="/home/z/my-project/download",
        help="输出目录",
    )
    args = ap.parse_args()

    in_path = Path(args.input)
    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bin_data = in_path.read_bytes()
    n_frames = len(bin_data) // proto.FRAME_SIZE
    print(f"[M3] 输入: {in_path.name} ({len(bin_data)} 字节, {n_frames} 帧)")

    # 1. 批量解码
    decoded = decode_multitime_messages(bin_data)
    print(f"[1] 批量解码完成: {len(decoded)} 帧")

    # 2. 分组、排序、track_sequence_no
    track_rows, rejected = group_and_sort_tracks(decoded)
    print(f"[2] 航迹分组完成: {len(track_rows)} 条可接受记录, "
          f"{len(rejected)} 条被拒绝")

    # 3. 当前态势
    current = build_current_situation(track_rows)
    print(f"[3] 当前态势生成: {len(current)} 个目标")

    # 4. 输出 CSV
    p_dec = out_dir / "decoded_multitime.csv"
    write_decoded_multitime(decoded, p_dec)
    print(f"[4] decoded_multitime.csv -> {p_dec}")

    p_track = out_dir / "track_table.csv"
    write_track_table(track_rows, p_track)
    print(f"[4] track_table.csv -> {p_track}")

    p_cur = out_dir / "current_situation.csv"
    write_current_situation(current, p_cur)
    print(f"[4] current_situation.csv -> {p_cur}")

    # 5. 汇总
    print("\n===== M3 输出汇总 =====")
    print(f"  decoded_multitime.csv  ({len(decoded)} 行)")
    print(f"  track_table.csv       ({len(track_rows)} 行)")
    print(f"  current_situation.csv ({len(current)} 行)")

    # 被拒绝记录
    if rejected:
        print(f"\n  被拒绝记录 ({len(rejected)} 条):")
        for r in rejected:
            errs = r.get("errors", [])
            err_types = [e[2] for e in errs] if errs else ["UNKNOWN"]
            print(f"    frame {r.get('frame_index')}: "
                  f"target_id={r.get('target_id')}, "
                  f"errors={err_types}")

    # 航迹分组概览
    print("\n  航迹分组概览:")
    groups: dict[str, list] = {}
    for row in track_rows:
        groups.setdefault(row["target_id"], []).append(row)
    for tid in sorted(groups.keys()):
        g = groups[tid]
        ts_list = [r["timestamp"] for r in g]
        print(f"    {tid}: {len(g)} 条记录, "
              f"timestamp {min(ts_list)} -> {max(ts_list)}")

    # 当前态势概览
    print("\n  当前态势概览:")
    for c in current:
        print(f"    {c['target_id']}: ts={c['timestamp']}, "
              f"lat={c.get('latitude')}, lon={c.get('longitude')}, "
              f"alt={c.get('altitude')}, "
              f"validity=lat{int(c['lat_valid'])}lon{int(c['lon_valid'])}"
              f"alt{int(c['altitude_valid'])}")


if __name__ == "__main__":
    main()
