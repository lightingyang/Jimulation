# 入参迁移指南：v1.0.8.6 → v1.0.9.5（管加工域）

> 本文档面向基于 1.0.8.6 版本开发的前端/客户端，说明管加工相关接口升级到 1.0.9.5 所需的全部变更。

---

## 目录

1. [总览：Breaking Changes 清单](#1-总览breaking-changes-清单)
2. [管加工仿真接口](#2-管加工仿真接口)
3. [管加工优化接口（新增）](#3-管加工优化接口新增)
4. [响应结构变更](#4-响应结构变更)
5. [完整示例对照](#5-完整示例对照)
6. [快速检查表](#6-快速检查表)

---

## 1. 总览：Breaking Changes 清单

| 变更项 | 旧版 (1.0.8.6) | 新版 (1.0.9.5) | 影响 |
|--------|----------------|----------------|------|
| 入参列表字段名 | `pipes_config` | `products_config` | **字段重命名** |
| 产品模型 | `PipeConfig` | `ProductConfig` | **结构变更** |
| 产品类型字段 | `pipe_type` | `product_type` | **字段重命名** |
| 产线筛选 | `line_filter` 存在 | 已移除 | **字段删除** |
| SegmentConfig | 无 `children` | 新增 `children: SegmentConfig[]` | **新增字段** |
| ProductConfig | 无 `process_codes` | 新增 `process_codes: string[]` | **新增字段** |
| ProductConfig | 无 `work_order` | 新增 `work_order: string` | **新增字段** |
| 装配机制 | 不支持 | 嵌套 `children` + 产品级 `process_codes` | **新功能** |
| 响应: `simulation_parameters` | 存在 | **已删除** | **字段删除** |
| 响应: `performance_metrics` | 存在 | **已删除** | **字段删除** |
| 响应: `simulation_summary` | 扁平结构 | `progress` 子对象 | **结构调整** |
| 新端点 | 无 | `POST /api/v1/pipe_simulation_optimization` | **新增接口** |

---

## 2. 管加工仿真接口

**端点**：`POST /api/v1/pipe_simulation_status`

### 2.1 请求结构对比

#### 旧版 (1.0.8.6)

```typescript
interface PipeSimulationRequest {
  config_type: string;              // "CHIZY"
  pipes_config: PipeConfig[];       // ← 旧字段名
  line_filter?: string[] | null;    // ← 已移除
  simulation_duration: number;
  daily_work_time?: number;         // 默认 480
  device_adjustments?: DeviceAdjustment[];
}

interface PipeConfig {
  product_id: string;
  pipe_type?: string;               // ← 旧字段名，默认 "standard"
  segments: SegmentConfig[];
}

interface SegmentConfig {
  segment_id: string;
  process_codes: string[];
}
```

#### 新版 (1.0.9.5)

```typescript
interface PipeSimulationRequest {
  config_type: string;              // "CHIZY"
  products_config: ProductConfig[]; // ← 改名！
  // line_filter 已删除
  simulation_duration: number;
  daily_work_time?: number;         // 默认 480
  device_adjustments?: DeviceAdjustment[];
}

interface ProductConfig {
  product_id: string;
  product_type?: string;            // ← 改名！默认 "standard"
  work_order?: string;              // ← 新增，默认 ""
  process_codes?: string[];         // ← 新增，装配后工序，默认 []
  segments: SegmentConfig[];
}

interface SegmentConfig {
  segment_id: string;
  process_codes: string[];
  children?: SegmentConfig[];       // ← 新增，子件列表，默认 []
}

// DeviceAdjustment 不变
interface DeviceAdjustment {
  device_name: string;
  start_time: number;
  end_time: number;
  adjusted_time?: number;
  count?: number;
}
```

### 2.2 最小改动清单（无装配场景）

如果你的管加工场景**不涉及装配**（每个产品只有独立的 segments，无父子关系），只需：

1. **`pipes_config` → `products_config`**
2. **`pipe_type` → `product_type`**
3. **删除 `line_filter`**（如果使用了的话）
4. 其他字段保持不变，`children` 和 `process_codes` 不传即可（默认空）

### 2.3 逐字段对照

| 旧字段路径 | 新字段路径 | 说明 |
|-----------|-----------|------|
| `pipes_config` | `products_config` | 顶层列表字段名 |
| `pipes_config[].product_id` | `products_config[].product_id` | 不变 |
| `pipes_config[].pipe_type` | `products_config[].product_type` | 重命名 |
| （不存在） | `products_config[].work_order` | 新增，可选，默认 `""` |
| （不存在） | `products_config[].process_codes` | 新增，可选，装配后工序 |
| `pipes_config[].segments[].segment_id` | `products_config[].segments[].segment_id` | 不变 |
| `pipes_config[].segments[].process_codes` | `products_config[].segments[].process_codes` | 不变 |
| （不存在） | `products_config[].segments[].children` | 新增，可选，子件列表 |
| `line_filter` | （已删除） | 不再支持 |
| `config_type` | `config_type` | 不变 |
| `simulation_duration` | `simulation_duration` | 不变 |
| `daily_work_time` | `daily_work_time` | 不变 |
| `device_adjustments` | `device_adjustments` | 不变（子字段也不变） |

### 2.4 装配树详解（新能力）

> 1.0.8.6 没有装配概念，所有 segment 都是独立并行加工的。
> 1.0.9.5 引入了**装配树**，可以表达"某些件必须先完成，另一个件才能开始"的依赖关系。

#### 什么是装配树？

装配树描述的是**工件之间的等待关系**。在制造场景中，经常有这样的需求：

- 管加工：先把小管段焊好（子件），再把多根小管组焊成大管（父件）
- 集装箱：五个面板（前框、后框、侧板、底架、顶板）各自加工完，再总装成箱子

装配树的核心规则只有一条：**父件会等待所有子件加工完毕后，才开始执行自己的工序**。子件之间是并行的，互不等待。

#### 入参中如何表达装配关系？

有两个入口可以建立装配关系，可以单独使用也可以组合使用：

| 入口 | 位置 | 作用 |
|------|------|------|
| `children` | `SegmentConfig` 内 | segment 级别的父子关系：父 segment 等子 segment 完成 |
| `process_codes` | `ProductConfig` 顶层 | 产品级别的后处理工序：等**所有** segments 完成后执行 |

#### 规则一：`children` — segment 级装配

一个 segment 如果有 `children`，它就是**父件**。它不会立即执行自己的 `process_codes`，而是：

1. 先启动所有 children（并行加工）
2. 等待 **全部** children 完成
3. 然后才执行自己的 `process_codes`

```
父 segment（有 children）
  ├── 子 segment A （并行）
  ├── 子 segment B （并行）
  └── 子 segment C （并行）
        ↓ 全部完成
  父 segment 开始执行自己的工序
```

**入参示例**：一根大管由两根小管焊接而成

```json
{
  "products_config": [
    {
      "product_id": "big_pipe_001",
      "product_type": "Z1",
      "segments": [
        {
          "segment_id": "big_pipe",
          "process_codes": ["Z1DGH"],
          "children": [
            {"segment_id": "small_pipe_a", "process_codes": ["Z1QD", "Z1JJG"]},
            {"segment_id": "small_pipe_b", "process_codes": ["Z1QD", "Z1JJG"]}
          ]
        }
      ]
    }
  ],
  "config_type": "CHIZY",
  "simulation_duration": 2400,
  "daily_work_time": 480
}
```

**执行流程**：

```
时间轴 →

small_pipe_a: ──[Z1QD]──[Z1JJG]──┐
                                   ├── 等待 ──→ big_pipe: ──[Z1DGH]──
small_pipe_b: ──[Z1QD]──[Z1JJG]──┘
```

- `small_pipe_a` 和 `small_pipe_b` 同时开始，各自独立做 Z1QD → Z1JJG
- 两个都完成后，`big_pipe` 才开始做 Z1DGH

#### 规则二：`process_codes` — 产品级后处理

在 `ProductConfig` 顶层设置 `process_codes`，表示**所有 segments 都完成后**，产品还要再经过这些工序。

系统内部会自动创建一个隐含的"父 segment"（ID 等于 `product_id`），它的子件就是所有顶层 segments。

```
ProductConfig
  ├── segment A （并行）
  ├── segment B （并行）
  └── segment C （并行）
        ↓ 全部完成
  执行 process_codes 中的工序（顺序执行）
```

**入参示例**：集装箱场景（但管加工也可以用）

```json
{
  "products_config": [
    {
      "product_id": "box_001",
      "product_type": "standard",
      "process_codes": ["总装", "油漆"],
      "segments": [
        {"segment_id": "front",  "process_codes": ["前框"]},
        {"segment_id": "back",   "process_codes": ["后框"]},
        {"segment_id": "side",   "process_codes": ["侧板"]},
        {"segment_id": "bottom", "process_codes": ["底架"]},
        {"segment_id": "top",    "process_codes": ["顶板"]}
      ]
    }
  ],
  "config_type": "DFICNB",
  "simulation_duration": 9600,
  "daily_work_time": 480
}
```

**执行流程**：

```
时间轴 →

front:  ──[前框]──────────┐
back:   ──[后框]────┐     │
side:   ──[侧板]────┤     ├── 等待全部 ──→ [总装] ──→ [油漆]
bottom: ──[底架]────┤     │
top:    ──[顶板]──────────┘
```

#### 规则三：同名子件（吞噬模式）

当子件的 `segment_id` 和父件**完全相同**时，系统自动将子件重命名为 `{id}_pre`。

这适用于"同一根管子先做预处理，再做主工序"的场景——逻辑上是同一个工件，但存在前后依赖。

```json
{
  "segments": [
    {
      "segment_id": "pipe_001",
      "process_codes": ["Z1DGH"],
      "children": [
        {
          "segment_id": "pipe_001",
          "process_codes": ["Z1QD", "Z1JJG"]
        }
      ]
    }
  ]
}
```

**系统内部处理**：

```
入参中的 segment_id        系统实际使用的 ID
─────────────────         ──────────────────
pipe_001（父件）      →   pipe_001
pipe_001（子件）      →   pipe_001_pre     ← 自动重命名
```

**执行流程**：

```
pipe_001_pre: ──[Z1QD]──[Z1JJG]──┐
                                   ├── 等待 ──→ pipe_001: ──[Z1DGH]──
                                  ┘
```

如果有多个同名子件，后续的会被命名为 `pipe_001_pre2`、`pipe_001_pre3`...

#### 规则四：多级嵌套

`children` 里的 segment 还可以有自己的 `children`，形成多级装配树。每一级的规则都一样：父等子完。

```json
{
  "segments": [
    {
      "segment_id": "final_assembly",
      "process_codes": ["Z1DGH"],
      "children": [
        {
          "segment_id": "sub_assembly_a",
          "process_codes": ["Z1XGH"],
          "children": [
            {"segment_id": "part_a1", "process_codes": ["Z1QD"]},
            {"segment_id": "part_a2", "process_codes": ["Z1QD"]}
          ]
        },
        {
          "segment_id": "sub_assembly_b",
          "process_codes": ["Z1XGH"],
          "children": [
            {"segment_id": "part_b1", "process_codes": ["Z1QD"]}
          ]
        }
      ]
    }
  ]
}
```

**执行流程**：

```
第1级                  第2级                 第3级

part_a1: ──[Z1QD]──┐
                    ├→ sub_assembly_a: ──[Z1XGH]──┐
part_a2: ──[Z1QD]──┘                              │
                                                    ├→ final_assembly: ──[Z1DGH]──
part_b1: ──[Z1QD]──→  sub_assembly_b: ──[Z1XGH]──┘
```

#### 规则五：不需要装配？什么都不用传

如果你的场景没有任何父子依赖（旧版 1.0.8.6 就是这样），完全不用关心装配树：

- 不传 `children`（默认 `[]`）
- 不传产品级 `process_codes`（默认 `[]`）

所有 segments 会像旧版一样独立并行加工，行为完全一致。

#### 总结：两张图理解装配树

**无装配（旧版行为，默认）**：
```
product
  ├── segment_1: ──[工序A]──[工序B]──  (独立)
  └── segment_2: ──[工序C]──           (独立)
```

**有装配（新版能力）**：
```
product
  └── 父 segment（等待子件）
        ├── 子 segment_1: ──[工序A]──┐
        └── 子 segment_2: ──[工序B]──┤
                                      ↓ 全部完成
        父 segment: ──[工序C]──[工序D]──
```

#### 常见问题

**Q: `children` 和 `process_codes` 可以同时用吗？**
A: 可以。segment 级 `children` 处理 segment 之间的父子关系；产品级 `process_codes` 处理所有顶层 segment 完成后的后续工序。两者可以叠加。

**Q: 一个产品可以有多个顶层 segment 并且其中一些有 children 吗？**
A: 可以。每个顶层 segment 独立处理。有 `children` 的等子件，没有的直接开始。

**Q: 子件的 `process_codes` 可以有多个工序吗？**
A: 可以。子件的多个工序按顺序执行（和普通 segment 一样），全部做完才算子件完成。

**Q: 如果不传 `children` 和 `process_codes`，行为和旧版一样吗？**
A: 完全一样。装配树是可选的，不传就是纯并行加工。

---

## 3. 管加工优化接口（新增）

**端点**：`POST /api/v1/pipe_simulation_optimization`

入参结构与 `pipe_simulation_status` **完全相同**（`PipeSimulationRequest`），返回优化建议。

```typescript
// 响应
interface OptimizationRecommendationResponse {
  recommendations: Recommendation[];      // 优化建议列表
  comparison: SimulationComparison;       // 优化前后对比
  baseline_summary: object;               // 基线仿真摘要
  optimized_summary: object;              // 优化后仿真摘要
  device_adjustments: DeviceAdjustment[]; // 生成的设备调整方案
}

interface Recommendation {
  device_type: string;                    // 设备类型
  recommendation_type: "extend_work_hours" | "add_devices";
  target_days: number[];                  // 目标天数
  additional_hours?: number;              // 建议加班时长
  additional_devices?: number;            // 建议增加设备数
  reason: string;                         // 建议原因
}

interface SimulationComparison {
  baseline_completion_time: number;       // 基线完成时间（分钟）
  optimized_completion_time: number;      // 优化后完成时间
  time_reduction_minutes: number;         // 减少时间
  time_reduction_percentage: number;      // 减少百分比
}
```

---

## 4. 响应结构变更

### 4.1 `PipeAnalysisResponse`

| 字段 | 旧版 | 新版 | 说明 |
|------|------|------|------|
| `simulation_summary` | 扁平结构 | 含 `progress` 子对象 | 结构调整，见下方 |
| `simulation_parameters` | 存在 | **已删除** | 移除 |
| `performance_metrics` | 存在 | **已删除** | 移除 |
| `total_device_utilization` | 存在 | 不变 | |
| `utilization_analysis` | 存在 | 不变 | |
| `daily_segments` | 存在 | 不变 | |
| `simulation_details` | 存在 | 不变 | |

### 4.2 `simulation_summary` 结构变化

```jsonc
// ========== 旧版 ==========
{
  "simulation_summary": {
    "simulation_time": 2400,
    "effective_time": 1920,
    "total_products": 10,
    "completed_products": 10,
    "failed_products": 0
  }
}

// ========== 新版 ==========
{
  "simulation_summary": {
    "simulation_time": 2400,
    "effective_time": 1920,
    "daily_work_time": 480,             // 新增
    "total_pipes": 10,                  // 新增
    "progress": {                       // ← 原来的字段移入此子对象
      "total_products": 10,
      "completed_products": 10,
      "failed_products": 0
    }
  }
}
```

**前端适配要点**：

- `simulation_summary.total_products` → `simulation_summary.progress.total_products`
- `simulation_summary.completed_products` → `simulation_summary.progress.completed_products`
- `simulation_summary.failed_products` → `simulation_summary.progress.failed_products`
- 新增 `daily_work_time` 和 `total_pipes` 可按需使用
- 删除对 `simulation_parameters` 和 `performance_metrics` 的引用

### 4.3 旧版被删除字段的替代方案

| 旧版字段 | 替代方式 |
|----------|---------|
| `simulation_parameters.simulation_duration` | 前端本地保存请求参数即可 |
| `simulation_parameters.daily_work_time` | `simulation_summary.daily_work_time` |
| `simulation_parameters.total_pipes` | `simulation_summary.total_pipes` |
| `simulation_parameters.line_filter` | 已无此功能 |
| `performance_metrics.average_completion_time` | 需自行从 `simulation_details` 计算 |
| `performance_metrics.max_completion_time` | `simulation_summary.effective_time` |
| `performance_metrics.throughput_utilization` | 需自行从 `utilization_analysis` 计算 |

---

## 5. 完整示例对照

### 5.1 管加工（无装配）— 最常见场景

```jsonc
// ========== 旧版 1.0.8.6 ==========
{
  "config_type": "CHIZY",
  "pipes_config": [
    {
      "product_id": "pipe_001",
      "pipe_type": "Z1",
      "segments": [
        {"segment_id": "seg_001", "process_codes": ["Z1QD", "Z1JJG", "Z1DGH"]}
      ]
    },
    {
      "product_id": "pipe_002",
      "pipe_type": "Z2",
      "segments": [
        {"segment_id": "seg_002", "process_codes": ["Z2QD", "Z2JJG"]}
      ]
    }
  ],
  "line_filter": ["Z1"],
  "simulation_duration": 2400,
  "daily_work_time": 480
}

// ========== 新版 1.0.9.5 ==========
{
  "config_type": "CHIZY",
  "products_config": [
    {
      "product_id": "pipe_001",
      "product_type": "Z1",
      "segments": [
        {"segment_id": "seg_001", "process_codes": ["Z1QD", "Z1JJG", "Z1DGH"]}
      ]
    },
    {
      "product_id": "pipe_002",
      "product_type": "Z2",
      "segments": [
        {"segment_id": "seg_002", "process_codes": ["Z2QD", "Z2JJG"]}
      ]
    }
  ],
  "simulation_duration": 2400,
  "daily_work_time": 480
}
```

**改动点**：`pipes_config` → `products_config`，`pipe_type` → `product_type`，删除 `line_filter`。

### 5.2 管加工（含设备调整）

```jsonc
// ========== 旧版 1.0.8.6 ==========
{
  "config_type": "CHIZY",
  "pipes_config": [
    {
      "product_id": "pipe_001",
      "pipe_type": "Z1",
      "segments": [
        {"segment_id": "seg_001", "process_codes": ["Z1QD", "Z1JJG"]}
      ]
    }
  ],
  "simulation_duration": 2400,
  "daily_work_time": 480,
  "device_adjustments": [
    {
      "device_name": "Z1QD",
      "start_time": 480,
      "end_time": 960,
      "adjusted_time": 600,
      "count": 2
    }
  ]
}

// ========== 新版 1.0.9.5 ==========
{
  "config_type": "CHIZY",
  "products_config": [
    {
      "product_id": "pipe_001",
      "product_type": "Z1",
      "segments": [
        {"segment_id": "seg_001", "process_codes": ["Z1QD", "Z1JJG"]}
      ]
    }
  ],
  "simulation_duration": 2400,
  "daily_work_time": 480,
  "device_adjustments": [
    {
      "device_name": "Z1QD",
      "start_time": 480,
      "end_time": 960,
      "adjusted_time": 600,
      "count": 2
    }
  ]
}
```

**改动点**：仅 `pipes_config` → `products_config`，`pipe_type` → `product_type`。`device_adjustments` 结构完全不变。

### 5.3 管加工（有装配：吞噬模式）

```jsonc
// ========== 新版 1.0.9.5（旧版无此功能）==========
{
  "config_type": "CHIZY",
  "products_config": [
    {
      "product_id": "pipe_001",
      "product_type": "Z1",
      "segments": [
        {
          "segment_id": "pipe_001",
          "process_codes": ["Z1QD"],
          "children": [
            {
              "segment_id": "pipe_001",
              "process_codes": ["Z1JJG"]
            }
          ]
        }
      ]
    }
  ],
  "simulation_duration": 2400,
  "daily_work_time": 480
}
```

**说明**：子件 `segment_id` 与父件相同时，系统自动重命名为 `pipe_001_pre`，子件完成后父件才开始执行。

---

## 6. 快速检查表

在迁移代码时，逐项确认：

**请求入参：**
- [ ] `pipes_config` → `products_config`
- [ ] `pipe_type` → `product_type`
- [ ] 删除 `line_filter` 相关代码
- [ ] `device_adjustments` 不变，无需改动

**响应解析：**
- [ ] 删除对 `simulation_parameters` 的引用
- [ ] 删除对 `performance_metrics` 的引用
- [ ] `simulation_summary.total_products` → `simulation_summary.progress.total_products`
- [ ] `simulation_summary.completed_products` → `simulation_summary.progress.completed_products`
- [ ] `simulation_summary.failed_products` → `simulation_summary.progress.failed_products`

**可选（新功能）：**
- [ ] 如需装配功能，添加 `process_codes` 和/或 `children`
- [ ] 如需优化建议，对接新端点 `/pipe_simulation_optimization`
