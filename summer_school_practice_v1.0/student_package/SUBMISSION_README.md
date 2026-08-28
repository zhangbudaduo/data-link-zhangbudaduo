# SUBMISSION_README.md — 数据链软件暑期学校 M1-M6 综合运行说明

## 1. 学生信息

- 姓名：[学生姓名]
- 学号：[学号]
- GitHub 用户名：[GitHub用户名]

## 2. 运行环境

- 操作系统：Linux (Ubuntu)
- Python 版本：3.12
- 依赖库：标准库（csv, json, sqlite3, struct, math, pathlib）+ matplotlib（仅轨迹图选做）
- 无需额外安装第三方包；SQLite 使用 Python 内置 sqlite3 模块

## 3. 运行命令

```bash
# 进入项目目录
cd /home/z/my-project/scripts

# 一键运行 M6 综合演练（从空 output 目录重复运行）
python m6_pipeline.py

# 单独运行各模块（可选）
python m2_run.py        # M2 协议解析与消息编解码
python m3_tracks.py     # M3 单源多时刻关联与当前态势
python m4_run.py        # M4 语义互操作与大模型辅助映射
python m3_optional.py   # M3 选做：SQLite + 轨迹图
```

## 4. 输入文件

| 输入文件 | 路径 | 说明 |
|---------|------|------|
| raw_states.json | upload/ | OpenSky 教学样例，5 条状态向量 |
| partner_messages_multitime.bin | upload/ | TeachingLink 多时间片，9 帧 × 41 字节 |
| opensky_field_dictionary.csv | upload/ | OpenSky 字段字典 |
| partner_field_dictionary.csv | upload/ | TeachingLink 字段字典 |
| teaching_message_spec.md | upload/ | 41 字节帧规范 |
| source_field_definitions.md | upload/ | M4 两种来源字段定义 |
| unified_model.json | upload/ | 统一态势模型 |
| pre_generated_mapping_candidate.csv | upload/ | M4 预生成映射候选 |
| optional_db_schema.sql | upload/ | SQLite 建表脚本 |

## 5. 输出文件

### 必交材料（output/ 目录）

| 文件 | 模块 | 说明 |
|------|------|------|
| encoded_messages.bin | M2 | 4 帧 × 41 字节 = 164 字节二进制消息 |
| decoded_partner_states.csv | M2 | 4 行解码结果 |
| validation_log.csv | M2 | 字段与帧级错误记录 |
| roundtrip_report.csv | M2 | 40 行往返对比 |
| decoded_multitime.csv | M3 | 9 行批量解码结果 |
| track_table.csv | M3 | 9 行航迹表（3 目标 × 3 时刻） |
| current_situation.csv | M3 | 3 行当前态势 |
| llm_mapping_candidate.csv | M4 | 8 条映射候选 |
| verified_mapping_table.csv | M4 | 29 条人工核验映射 |
| unified_situation.ndjson | M4 | 6 行统一态势（OpenSky 3 + TeachingLink 3） |
| alert_log.csv | M5 | 5 条告警（POSITION_MISSING 2 + DUPLICATE_RECORD 3） |
| quality_situation.csv | M5 | 6 行质量增强态势 |

### 选做材料

| 文件 | 说明 |
|------|------|
| states.db | SQLite 数据库，state_record 表 |
| trajectory_plot.png | 3 目标经纬度轨迹图 |

### 代码文件

| 文件 | 说明 |
|------|------|
| m2_protocol.py | M2 协议解析与编解码模块 |
| m3_tracks.py | M3 航迹管理模块 |
| m4_mapping.py | M4 语义映射模块 |
| m5_quality.py | M5 一致性检查模块 |
| m6_pipeline.py | M6 综合演练统一入口 |

## 6. 数据量与帧数

- OpenSky 输入：5 条状态向量，有效 4 条（1 条时间戳缺失被拒绝）
- TeachingLink 多时间片：9 帧 × 41 字节 = 369 字节
- 编码输出：4 帧 × 41 字节 = 164 字节
- 航迹：3 个目标 × 3 个时间片 = 9 条航迹记录
- 当前态势：3 个目标各 1 条最新记录
- 统一态势：6 条（OpenSky 3 + TeachingLink 3）
- 告警：5 条（POSITION_MISSING 2 + DUPLICATE_RECORD 3）

## 7. 映射来源

M4 映射候选来源为 `pre_generated_mapping_candidate.csv`（预生成，模拟大模型输出）。
经人工核验发现 4 类错误并修正：
1. 经纬度字段颠倒（latitude_code↔longitude_code 映射到错误的 position.lat/lon）
2. 高度物理偏置遗漏（应为 code-1000，候选写"code 乘 1 米"）
3. status_flags.bit2 语义误判（应为 time_source，候选误标为 time_valid）
4. 覆盖不全（缺 speed/heading/vrate/on_ground/alt_type 等 21 条映射）

最终映射使用人工核验后的 `verified_mapping_table.csv`（29 条），每条含规则、单位转换、空值策略、证据和 verified 字段。

## 8. 实验结果

### M2 编解码
- 4 帧全部通过校验（magic/version/type/length/checksum）
- 往返通过率 100%（有效字段误差均在一个量化单位内）
- 正确区分真实零值（000001 全零）与字段缺失（780def speed 缺失）

### M3 航迹与当前态势
- 3 个目标各 3 条记录，track_sequence_no 从 1 开始连续编号
- 780def 第 3 条记录 lat/lon 无效但仍纳入航迹（字段缺失不阻断关联）
- 时间源回退正确处理（780def 第 1 条 timestamp_source=last_contact）

### M4 语义映射
- 两种来源均成功映射到统一模型
- 780def 的 alt_type 差异（OpenSky=barometric vs TeachingLink=geometric）为语义来源差异

### M5 一致性检查
- R1 POSITION_MISSING：780def lat/lon 为空（HIGH）
- R3 DUPLICATE_RECORD：TeachingLink 与 OpenSky 同 target_id+timestamp（MEDIUM）
- 显示状态：780def=ERROR，000001/780abc=WARNING（因重复告警）

## 9. 已知限制

1. TeachingLink 为学校自定义教学协议，不对应任何行业标准
2. message_valid 仅代表帧通过格式与校验检查，不代表来源真实性或安全完整性
3. OpenSky altitude_type 列为空时默认 barometric（baro_altitude 为优先高度源）
4. M5 重复检查将 OpenSky 与 TeachingLink 同目标同时间记录判为重复，实际为多源融合场景
5. 本实验为离线数据处理，不涉及真实网络通信、多传感器融合或真实装备接入
