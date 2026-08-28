# M4 AI 辅助映射核验说明

## 1. 候选来源

本实验的映射候选来源为 `pre_generated_mapping_candidate.csv`，共 8 条候选映射，覆盖 OpenSky 与 TeachingLink 两种来源。候选由预生成脚本产生，模拟大模型在给定字段定义、统一模型和输出表头后生成的映射建议。候选仅用于辅助，不要求其正确，最终结果必须由学生依据 `source_field_definitions.md`、`teaching_message_spec.md`、`opensky_field_dictionary.csv`、`partner_field_dictionary.csv` 和 `unified_model.json` 逐条人工核验。

候选覆盖的字段包括：track_id、timestamp、position.lat/lon、position.alt、identity.callsign、quality.time_source（候选误标为 time_valid）、quality.message_valid。候选未覆盖 speed、heading、vertical_rate、on_ground、alt_type、position_valid、time_valid 等字段，需在核验中补充。

## 2. 候选问题与修订依据

经逐条核验，发现候选存在以下 4 类错误，均已修正：

### 2.1 经纬度字段颠倒（严重）

- **候选错误**：`latitude_code+validity_flags.bit0 → position.lon`，`longitude_code+validity_flags.bit1 → position.lat`，将纬度映射到经度、经度映射到纬度。
- **修订依据**：`source_field_definitions.md` 明确规定 `position.lat` 来自 `latitude_code+validity_flags.bit0`，`position.lon` 来自 `longitude_code+validity_flags.bit1`。`teaching_message_spec.md` 的定点编码公式也确认：纬度 `Q((lat+90)/180*(2^22-1))`，经度 `Q((lon+180)/360*(2^22-1))`，两者量程不同（180° vs 360°），颠倒会导致物理量完全错误。
- **修正**：交换为正确映射，`latitude_code → position.lat`，`longitude_code → position.lon`。

### 2.2 高度物理偏置遗漏（严重）

- **候选错误**：`altitude_code+validity_flags.bit2 → position.alt`，规则写"code 乘 1 米"，遗漏了物理偏置 -1000。
- **修订依据**：`teaching_message_spec.md` 明确规定高度编码为 `Q(altitude_m + 1000)`，即 `code = altitude + 1000`，因此解码时 `altitude = code - 1000`。若仅乘 1 米，则 altitude=0 对应 code=0，而实际 code=0 对应 altitude=-1000m，会导致所有高度值偏大 1000 米。
- **修正**：规则改为 `code - 1000`，单位米，物理偏置 1000m。

### 2.3 status_flags.bit2 语义误判（严重）

- **候选错误**：`status_flags.bit2 → quality.time_valid`，规则"bit2 为 1 时设 false"。
- **修订依据**：`teaching_message_spec.md` 规定 `status_flags.bit2 = timestamp_fallback`，表示时间戳是否回退到 last_contact。这是时间来源标志，不是时间有效性标志。`source_field_definitions.md` 也确认 `quality.time_source` 来自 `status_flags.bit2`，值为 `position_time` 或 `last_contact_fallback`。时间回退不等于时间无效——回退时间仍可用，只是来源不同。
- **修正**：映射改为 `status_flags.bit2 → quality.time_source`，bit2=0 → position_time，bit2=1 → last_contact_fallback。

### 2.4 候选覆盖不全

候选缺少 21 条映射（speed、heading、vertical_rate、on_ground、alt_type、position_valid、time_valid、TeachingLink timestamp 等），均依据字段定义和协议规范补充。

## 3. 验证结果

### 3.1 真实零值样例验证

以目标 000001 为例：`vertical_rate_code=32768`，对应物理值 `32768×0.01−327.68 = 0.0 m/s`。这是真实零值（validity_flags.bit5=1，有效），统一模型中 `motion.vertical_rate=0.0`（非 null）。验证通过：有效位为 1 且协议整数为 0 表示真实物理零值，不会被误判为缺失。

### 3.2 字段缺失样例验证

以目标 780def 为例：`validity_flags=52`（bit2+bit4+bit5=1，bit0/1/3/6=0），即 lat/lon/speed/callsign 无效。统一模型中 `position.lat=null`、`position.lon=null`、`motion.speed=null`、`identity.callsign=null`，而 `altitude=7400.0`、`heading=268.0`、`vertical_rate=-0.8` 仍有效。验证通过：有效位为 0 时统一字段为 null，不会与真实零值混淆。

### 3.3 alt_type 与 time_source 验证

- 目标 780def 的 TeachingLink 记录 `status_flags=2`（bit1=1），映射为 `alt_type=geometric`；OpenSky 记录 `altitude_type` 为空，因高度有效且 baro_altitude 为优先源，默认 `barometric`。两者差异是语义来源差异，非映射错误。
- 所有记录 `status_flags.bit2=0`，映射为 `time_source=position_time`，与 OpenSky 的 `timestamp_source=time_position` 经归一化后一致。

### 3.4 NDJSON 可重新读取

`unified_situation.ndjson` 共 6 行（OpenSky 3 + TeachingLink 3），每行一个 JSON 对象，结构符合 `unified_model.json`。已验证可重新读取并解析。

## 4. 适用限制

1. **候选不是答案**：预生成候选存在 4 类错误，未经核验直接使用会导致经纬度颠倒、高度偏移 1000m、时间源误判等严重问题。
2. **alt_type 默认值**：OpenSky 当前态势的 `altitude_type` 列为空时默认 barometric，因 OpenSky 以 baro_altitude 为优先高度源；若实际使用 geo_altitude，需补充来源判断。
3. **message_valid 语义**：`message_valid` 仅代表帧通过格式与校验检查，不代表来源真实性或安全完整性，不得扩大为来源可信。
4. **time_source 归一化**：OpenSky 的 `time_position`/`last_contact` 与统一模型的 `position_time`/`last_contact_fallback` 存在命名差异，已在映射中归一化。
5. **本实验为离线教学**：不涉及真实网络通信、多传感器融合或真实装备接入，映射规则仅适用于教学数据。
