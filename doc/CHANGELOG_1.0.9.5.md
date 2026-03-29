# Jimulation 1.0.9.5 Changelog

**发布日期：** 2026-03-29

---

## 装配机制重构（Breaking Change）

统一装配入参格式，移除 `assembly_tree` 字段，改用嵌套 `children` + 产品级 `process_codes`。

### 入参变更

- `ProductConfig`：
  - 移除：`assembly_tree: Dict[str, List[str]]`
  - 新增：`process_codes: string[]`（产品级装配后工序，子件全部完成后执行）
- `SegmentConfig`：
  - 新增：`children: SegmentConfig[]`（子件列表，有 children 即为装配）
  - 子件的 `segment_id` 可以与父件相同（吞噬模式），系统自动重命名为 `{id}_pre`

### 装配逻辑统一

- 移除 inline 装配（同码自动汇合）和 tree 装配（segment 完成后检查父节点）两套机制
- 统一为：父件等待所有子件完成 → 执行装配工序（时长 × 子件数）→ 继续后续工序
- 修复：装配后父件的后续工序（如油漆）不执行的 bug

### 支持场景

1. **一级装配**（集装箱）：产品级 `process_codes: ["总装", "油漆"]` + 子面板 segments
2. **同名子件/吞噬**（管加工）：`children` 中子件 segment_id 与父件相同
3. **多级装配**：segment 的 `children` 嵌套
4. **无装配**：无 `process_codes`、无 `children` → 纯并行处理

### 受影响文件

- `api/models.py` — 入参模型
- `api/_helpers.py` — 新增 `flatten_product_config()` 嵌套→扁平转换
- `core/assembly_coordinator.py` — 简化为事件驱动的子件完成通知
- `core/process_flow.py` — 统一装配等待逻辑
- `core/simulation.py` — 父 segment 也启动 SimPy 进程

## 文档

- 更新 `doc/coil_uncoiling_estimate_API.md` 至 1.0.9.5 版本
- 新增 `doc/CHANGELOG_1.0.9.5.md`
