# Jimulation 1.0.9.2 Changelog

**发布日期：** 2026-03-25

---

## 文档更新

- 主接口文档版本号升级至 1.0.9.2
- 预处理线接口文档同步补齐 1.0.9.1 新增字段：
  - 入参 `CoilPartConfig` 新增 `spec_prefix`（规格前缀）
  - 出参 `CoilSpecSummary` 新增 `spec_prefix`、`coil_count`（卷数）、`splicing_time_minutes`（接板时间）
  - 规格聚合维度说明更新为 `(前缀×长×宽×厚)`
  - 业务规则补充卷数和接板时间计算公式
  - 请求/响应示例同步更新
- 预处理线接口支持入参 `preprocessing_lead_time_minutes`，默认 `2880` 分钟；排产时按 `ceil(分钟数 / 1440)` 换算为提前天数，替代原先写死的提前 2 天规则
