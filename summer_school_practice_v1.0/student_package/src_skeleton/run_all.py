"""M6 综合演练 —— 统一入口，串联 M2-M5 为可重复运行的数据链处理链。

从 raw_states.json 和 partner_messages_multitime.bin 启动，依次完成：
  1. parse_open_source()           OpenSky 解析（M2 parse_state_vector）
  2. encode_teaching_messages()    41 字节帧编码（M2 encode）
  3. decode_and_validate_messages() 接收解码与校验（M2 decode）
  4. persist_records()             选做 SQLite 持久化
  5. build_tracks_and_situation()  航迹表与当前态势（M3）
  6. map_with_verified_rules()     统一消息映射（M4）
  7. consistency_check()           一致性检查与告警（M5）
  8. export_results()              汇总导出

可从空 output 目录重复运行，不修改原始输入。
"""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import m2_protocol as proto  # noqa: E402
import m3_tracks as m3  # noqa: E402
import m4_mapping as m4  # noqa: E402
import m5_quality as m5  # noqa: E402

UPLOAD = Path("/home/z/my-project/upload")
OUTPUT = Path("/home/z/my-project/download")

# 输入
RAW_STATES = UPLOAD / "6a8d566d42013bd4e7b847b8_raw_states.json"
MULTITIME_BIN = UPLOAD / "6a8eb32741e8b2607a23d0c4_partner_messages_multitime.bin"
CANDIDATE_CSV = UPLOAD / "6a8ecef449dbcd546dd1a085_pre_generated_mapping_candidate.csv"
PARTNER_CS = UPLOAD / "6a8eceb042013bd4e7bd857f_partner_current_situation.csv"
SCHEMA_SQL = UPLOAD / "6a8eb34014c1ab51600ca09e_optional_db_schema.sql"


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


def main():
    print("=" * 70)
    print("M6 综合演练 —— M2-M5 统一数据链处理链")
    print("=" * 70)

    # ---- 0. 清空 output 目录（可选，保证可重复运行）----
    # 保留 README、报告、代码和展示材料，只清空数据文件
    keep_prefixes = ("README", "M2_", "M3_", "M4_", "M5_", "M6_",
                     "m2_", "m3_", "m4_", "m5_", "m6_", "SUBMISSION")
    for f in OUTPUT.iterdir():
        if f.is_file() and not f.name.startswith(keep_prefixes):
            f.unlink()
    print("[0] output 目录已清空（保留代码、报告和展示材料）\n")

    # ---- 1. parse_open_source() ----
    print("[1] parse_open_source: OpenSky 状态向量解析")
    raw = json.loads(RAW_STATES.read_text(encoding="utf-8"))
    states = raw["states"]
    parsed = []
    for i, vec in enumerate(states):
        rec = proto.parse_state_vector(vec)
        rec["record_no"] = i
        parsed.append(rec)
    valid_parsed = [r for r in parsed if r.get("target_id") and r.get("timestamp")]
    print(f"    解析 {len(states)} 条，有效 {len(valid_parsed)} 条")

    # ---- 2. encode_teaching_messages() ----
    print("\n[2] encode_teaching_messages: 41 字节帧编码")
    frames = []
    for i, rec in enumerate(valid_parsed):
        frame = proto.encode_position_message(rec, i)
        frames.append(frame)
    bin_data = b"".join(frames)
    (OUTPUT / "encoded_messages.bin").write_bytes(bin_data)
    print(f"    编码 {len(frames)} 帧 -> encoded_messages.bin ({len(bin_data)} 字节)")

    # ---- 3. decode_and_validate_messages() ----
    print("\n[3] decode_and_validate_messages: 接收解码与校验")
    decoded = []
    validation_entries = []
    for i, frame in enumerate(frames):
        dec = proto.decode_position_message(frame)
        dec["record_no"] = i
        decoded.append(dec)
        for stage, field, ptype, value, desc in dec.get("errors", []):
            validation_entries.append({
                "record_no": i,
                "stage": stage,
                "field": field,
                "problem_type": ptype,
                "value": value,
                "description": desc,
                "message_valid": dec["message_valid"],
                "source": "encoded_messages.bin",
            })
    # 写 decoded_partner_states.csv
    write_decoded_csv(decoded, OUTPUT / "decoded_partner_states.csv")
    write_validation_log(validation_entries, OUTPUT / "validation_log.csv")
    print(f"    解码 {len(decoded)} 帧 -> decoded_partner_states.csv")
    print(f"    校验日志 {len(validation_entries)} 条 -> validation_log.csv")

    # roundtrip_report.csv
    rt_rows = build_roundtrip(valid_parsed, decoded)
    write_roundtrip_csv(rt_rows, OUTPUT / "roundtrip_report.csv")
    passed = sum(1 for r in rt_rows if r["passed"])
    print(f"    往返报告 {len(rt_rows)} 行 (通过 {passed}) -> roundtrip_report.csv")

    # ---- 4. persist_records() (选做 SQLite) ----
    print("\n[4] persist_records: SQLite 持久化 (选做)")
    db_path = OUTPUT / "states.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    schema = SCHEMA_SQL.read_text(encoding="utf-8")
    cur.executescript(schema)
    for dec in decoded:
        if dec.get("message_valid"):
            cur.execute(
                """INSERT INTO state_record
                   (target_id, callsign, timestamp, timestamp_source, message_seq,
                    lat, lon, altitude, alt_type, speed, heading, vertical_rate,
                    on_ground, status_flags, validity_flags, message_valid, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dec.get("target_id"), dec.get("callsign"), dec.get("timestamp"),
                 dec.get("timestamp_source"), dec.get("message_seq"),
                 dec.get("lat"), dec.get("lon"), dec.get("altitude"),
                 dec.get("alt_type"), dec.get("speed"), dec.get("heading"),
                 dec.get("vertical_rate"), int(dec.get("on_ground", False)),
                 dec.get("status_flags"), dec.get("validity_flags"),
                 1, "m6_pipeline"),
            )
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM state_record")
    db_count = cur.fetchone()[0]
    conn.close()
    print(f"    states.db: {db_count} 条记录")

    # ---- 5. build_tracks_and_situation() ----
    print("\n[5] build_tracks_and_situation: 航迹表与当前态势")
    # 使用 partner_messages_multitime.bin（多时间片）
    multi_data = MULTITIME_BIN.read_bytes()
    multi_decoded = m3.decode_multitime_messages(multi_data)
    track_rows, rejected = m3.group_and_sort_tracks(multi_decoded)
    current = m3.build_current_situation(track_rows)

    m3.write_decoded_multitime(multi_decoded, OUTPUT / "decoded_multitime.csv")
    m3.write_track_table(track_rows, OUTPUT / "track_table.csv")
    m3.write_current_situation(current, OUTPUT / "current_situation.csv")
    print(f"    decoded_multitime.csv: {len(multi_decoded)} 行")
    print(f"    track_table.csv: {len(track_rows)} 行")
    print(f"    current_situation.csv: {len(current)} 行")

    # ---- 6. map_with_verified_rules() ----
    print("\n[6] map_with_verified_rules: 统一消息映射")
    # 候选
    candidate_rows = read_csv(CANDIDATE_CSV)
    write_candidate(candidate_rows, OUTPUT / "llm_mapping_candidate.csv")
    # 核验
    verified = m4.verify_candidate_mapping(candidate_rows)
    write_verified(verified, OUTPUT / "verified_mapping_table.csv")
    print(f"    llm_mapping_candidate.csv: {len(candidate_rows)} 条候选")
    print(f"    verified_mapping_table.csv: {len(verified)} 条正式映射")

    # 统一态势 NDJSON
    opensky_cs = read_csv(OUTPUT / "current_situation.csv")
    partner_cs = read_csv(PARTNER_CS)
    unified = []
    for r in opensky_cs:
        unified.append(m4.map_to_unified(r, "OpenSky"))
    for r in partner_cs:
        unified.append(m4.map_to_unified(r, "TeachingLink"))
    with open(OUTPUT / "unified_situation.ndjson", "w", encoding="utf-8") as f:
        for u in unified:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    print(f"    unified_situation.ndjson: {len(unified)} 行")

    # ---- 7. consistency_check() ----
    print("\n[7] consistency_check: 一致性检查与告警")
    alerts = m5.run_consistency_check(unified)
    quality = m5.build_quality_situation(unified, alerts)
    write_alert_log(alerts, OUTPUT / "alert_log.csv")
    write_quality_situation(quality, OUTPUT / "quality_situation.csv")
    print(f"    alert_log.csv: {len(alerts)} 条告警")
    print(f"    quality_situation.csv: {len(quality)} 行")

    # ---- 8. export_results() ----
    print("\n[8] export_results: 汇总")
    print("\n" + "=" * 70)
    print("M6 综合演练输出汇总")
    print("=" * 70)
    outputs = [
        "encoded_messages.bin", "decoded_partner_states.csv",
        "validation_log.csv", "roundtrip_report.csv",
        "decoded_multitime.csv", "track_table.csv", "current_situation.csv",
        "llm_mapping_candidate.csv", "verified_mapping_table.csv",
        "unified_situation.ndjson", "alert_log.csv", "quality_situation.csv",
        "states.db",
    ]
    for name in outputs:
        p = OUTPUT / name
        if p.exists():
            sz = p.stat().st_size
            print(f"  ✓ {name:35} ({sz} 字节)")
        else:
            print(f"  ✗ {name:35} 缺失!")

    # 告警分类
    from collections import Counter
    alert_types = Counter(a["alert_type"] for a in alerts)
    print(f"\n  告警分类: {dict(alert_types)}")
    status_counts = Counter(q["display_status"] for q in quality)
    print(f"  显示状态: {dict(status_counts)}")


# ---------------------------------------------------------------------------
# CSV 写出辅助函数
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_decoded_csv(rows, path):
    cols = ["target_id", "callsign", "timestamp", "timestamp_source", "time_source",
            "message_seq", "lat", "lon", "altitude", "alt_type", "speed", "heading",
            "vertical_rate", "on_ground", "status_flags", "validity_flags",
            "latitude_code", "longitude_code", "altitude_code", "speed_code",
            "heading_code", "vertical_rate_code", "lat_valid", "lon_valid",
            "altitude_valid", "speed_valid", "heading_valid", "vertical_rate_valid",
            "callsign_valid", "checksum", "expected_checksum", "message_valid",
            "validation_errors", "source"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in cols])


def write_validation_log(entries, path):
    cols = ["record_no", "stage", "field", "problem_type", "value",
            "description", "message_valid", "source"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for e in entries:
            w.writerow([fmt(e.get(c)) for c in cols])


def build_roundtrip(parsed, decoded):
    rows = []
    for i, (p, d) in enumerate(zip(parsed, decoded)):
        tid = p.get("target_id", "")
        fields = [
            ("latitude", p.get("latitude"), p.get("latitude_valid"),
             d.get("latitude_code"), d.get("lat_valid"), d.get("lat")),
            ("longitude", p.get("longitude"), p.get("longitude_valid"),
             d.get("longitude_code"), d.get("lon_valid"), d.get("lon")),
            ("altitude", p.get("altitude"), p.get("altitude_valid"),
             d.get("altitude_code"), d.get("altitude_valid"), d.get("altitude")),
            ("velocity", p.get("velocity"), p.get("velocity_valid"),
             d.get("speed_code"), d.get("speed_valid"), d.get("speed")),
            ("heading", p.get("heading"), p.get("heading_valid"),
             d.get("heading_code"), d.get("heading_valid"), d.get("heading")),
            ("vertical_rate", p.get("vertical_rate"), p.get("vertical_rate_valid"),
             d.get("vertical_rate_code"), d.get("vertical_rate_valid"),
             d.get("vertical_rate")),
            ("callsign", p.get("callsign"), p.get("callsign_valid"),
             None, d.get("callsign_valid"), d.get("callsign")),
            ("timestamp", p.get("timestamp"), True,
             None, True, d.get("timestamp")),
            ("target_id", tid, True, None, True, d.get("target_id")),
            ("on_ground", p.get("on_ground"), True,
             None, True, d.get("on_ground")),
        ]
        for fname, sv, svalid, code, dvalid, dv in fields:
            tol = {"latitude": 4.29e-5, "longitude": 8.58e-5,
                   "altitude": 1.0, "velocity": 0.1, "heading": 0.01,
                   "vertical_rate": 0.01}.get(fname)
            err = ""
            ratio = ""
            passed = True
            if sv is not None and dv is not None and tol is not None:
                err = abs(float(sv) - float(dv))
                ratio = err / tol
                passed = ratio <= 1.0
            elif svalid != dvalid:
                passed = False
            rows.append({
                "record_no": i, "target_id": tid, "field": fname,
                "source_value": sv, "source_valid": svalid,
                "protocol_code": code, "flag_bit": int(dvalid) if dvalid is not None else "",
                "decoded_value": dv, "decoded_valid": dvalid,
                "absolute_error": err, "tolerance": tol or "",
                "absolute_error/tolerance": ratio, "passed": passed,
            })
    return rows


def write_roundtrip_csv(rows, path):
    cols = ["record_no", "target_id", "field", "source_value", "source_valid",
            "protocol_code", "flag_bit", "decoded_value", "decoded_valid",
            "absolute_error", "tolerance", "absolute_error/tolerance", "passed"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in cols])


def write_candidate(rows, path):
    cols = ["source_format", "input_field", "candidate_unified_field",
            "candidate_rule", "confidence", "review_note"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])


def write_verified(rows, path):
    cols = ["source_format", "input_field", "unified_field", "rule",
            "unit_conversion", "null_strategy", "evidence", "verified",
            "candidate_issue"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])


def write_alert_log(alerts, path):
    cols = ["alert_time", "target_id", "alert_type", "severity", "field",
            "description"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for a in alerts:
            w.writerow([a.get(c, "") for c in cols])


def write_quality_situation(rows, path):
    cols = ["target_id", "timestamp", "position_valid", "delayed",
            "duplicate_detected", "heading_valid", "message_valid",
            "anomaly_level", "display_status"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in cols])


if __name__ == "__main__":
    main()
