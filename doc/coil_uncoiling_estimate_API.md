# Jimulation 1.0.9.4 接口文档

## 一、集装箱建造仿真接口

### 基本信息

| 项目 | 说明 |
|------|------|
| 路径 | `POST /api/v1/container_simulation_status` |
| 功能 | 运行集装箱建造仿真，统计各班组和设备工作时长 |
| Content-Type | `application/json` |

### 请求参数

#### ContainerSimulationRequest

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `config_type` | string | 否 | 配置类型，默认 `DFICNB` |
| `products_config` | ProductConfig[] | 是 | 产品配置列表 |
| `simulation_duration` | float | 是 | 仿真时长，单位分钟 |
| `daily_work_time` | float | 否 | 每日工作时间，默认 `480` 分钟 |

#### ProductConfig

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `product_id` | string | 是 | 产品唯一标识 |
| `product_type` | string | 否 | 产品类型，默认 `standard` |
| `work_order` | string | 否 | 工令号 |
| `process_codes` | string[] | 否 | 产品级后续工序 |
| `segments` | SegmentConfig[] | 是 | 产品包含的所有分段/部件 |

#### SegmentConfig

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `segment_id` | string | 是 | 分段或部件唯一标识 |
| `process_codes` | string[] | 是 | 工序编码列表 |
| `children` | SegmentConfig[] | 否 | 子部件列表 |

### 请求示例

```json
{
  "config_type": "DFICNB",
  "simulation_duration": 4800,
  "daily_work_time": 480,
  "products_config": [
    {
      "product_id": "CNT-001",
      "product_type": "standard",
      "work_order": "WO-2024-001",
      "process_codes": ["总装", "油漆"],
      "segments": [
        {"segment_id": "QK-001", "process_codes": ["前框"]},
        {"segment_id": "HK-001", "process_codes": ["后框"]},
        {"segment_id": "CB-001", "process_codes": ["侧板"]},
        {"segment_id": "DJ-001", "process_codes": ["底架"]},
        {"segment_id": "DB-001", "process_codes": ["顶板"]}
      ]
    }
  ]
}
```

### 响应参数

#### ContainerAnalysisResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| `simulation_duration` | float | 仿真时长 |
| `teams` | TeamWorkTime[] | 各班组/产线工作时长统计 |

#### TeamWorkTime

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 班组/产线名称 |
| `work_time` | float | 班组工作时长，单位分钟 |
| `work_order_times` | WorkOrderWorkTime[] | 按工令汇总的工作时长 |
| `devices` | DeviceWorkTime[] | 各设备工作时长 |

### 错误响应

| HTTP 状态码 | 场景 |
|-------------|------|
| 400 | 请求参数不合法 |
| 422 | 字段缺失或类型错误 |
| 500 | 服务端内部错误 |

---

## 二、预处理线钢卷开卷时间估算接口

### 基本信息

| 项目 | 说明 |
|------|------|
| 路径 | `POST /api/v1/coil_uncoiling_estimate` |
| 功能 | 根据工序天需求估算预处理线开卷排程，输出每日计划及缺口 |
| Content-Type | `application/json` |

### 业务规则

| 参数 | 说明 |
|------|------|
| 钢材密度 | `7.85 t/m^3` |
| 开卷线速度 | 读取配置，默认 `35 m/min` |
| 白班上限 | `720` 分钟/天 |
| 单日物理上限 | `1440` 分钟/天 |
| 厂商效率 | 读取 `config/Config_DFICNB.yaml` 的 `preprocessing.manufacturer_efficiency` |
| 长宽厚单位 | `mm` |

### 计算公式

- 单张重量(吨) = `length * width * thickness / 1e9 * 7.85`
- 单张开卷时间(分钟) = `length / 35000 / 厂商效率系数 / 设备OEE`
- 零件总重量 = `单张重量 * sheet_count`
- 零件总开卷时间 = `单张开卷时间 * sheet_count`

### 日期映射

- `offset = scheduling_window_days_or_total_days - max(process_day)`
- `scheduling_day = offset + process_day`
- `strict_latest_finish = scheduling_day * 1440 - lead_time_minutes`
- `relaxed_latest_finish = max(strict_latest_finish, scheduling_day * 1440 - 720)`
- 当 `lead_time_minutes = 2880` 时，表示优先按“至少提前 2 天”排；若严格窗口内仍有缺口，最多只会再动用“距工序天结束前半天”为止的缓冲

### 当前排程策略

1. 先把每个输入零件展开成内部任务，并计算各自的 `strict_latest_finish` 与 `relaxed_latest_finish`。
2. 将 `strict_latest_finish + relaxed_latest_finish + 规格 + 效率` 相同的任务合并为内部批次，避免把合法窗口不同的工件混在一起。
3. 每个批次先尽量在自己的严格 `lead_time` 窗口内排产；只有严格窗口排不下时，才会继续使用该批次专属的“`lead_time ~ 提前半天`”缓冲窗口。
4. 批次排序优先看窗口紧张度，即 `批次总工时 / 自身可排窗口总分钟数`，窗口越紧越优先。
5. 日内容量按两个 `12h` 段处理，因此既能表达“尽量白天 12h / 白夜班 24h”，也能表达“最多放宽到提前半天”。
6. 在合法窗口内，优先把已开工日压向 `12h` 或 `24h` 两个档位，并尽量复用已有同规格工作日，减少单日规格种类；其中 `11~13h` 视为已接近白班、`23~24h` 视为已接近白夜班，不再额外折腾。
7. 排程完成后会继续做整班整理：`0~11h` 尽量补到 `12h`，`13~23h` 尽量补到 `24h`，而 `<8h` 的短尾天会优先尝试并回前面已开工的天，以便多休一天。
8. 若传入 `rest_days`，调度会尽量优先使用其他工作日；只有在其他合法日期排不下时，才会动用这些休息日。同时会尽量让每个 7 天区间至少保留半天或一天休息，并优先把休息留在周末。
9. 若严格窗口、半天缓冲窗口以及非休息日都已尝试后仍无法完全排完，系统可以继续动用休息日产能；若仍不足，则剩余数量按原始工序天汇总到 `pre_stock_requirements`，作为库存缺口输出。
10. `pre_stock_requirements` 的每个 `day` 只表示该工序天自身仍未满足的缺口，不会把前序工序天的历史缺口滚动累加到后一天。

### 请求参数

#### CoilUncoilingRequest

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `total_days` | int | 是 | 总排产天数 |
| `rest_days` | int[] | 否 | 休息日数组，表示 `1 ~ total_days` 中希望尽量留空的天；调度会优先避开这些天，但它是偏好约束而不是硬约束，产能不足时仍可能安排生产；未传或传空数组时表示不启用休息日偏好 |
| `scheduling_window_days` | int | 否 | 有效预处理排产窗口期天数；未传时默认等于 `total_days` |
| `lead_time_minutes` | float | 否 | 预处理需提前完成的分钟数，默认 `2880` |
| `daily_parts` | DailyPartsConfig[] | 是 | 按工序天组织的零件需求 |

#### `rest_days` 说明

- `rest_days` 是偏好约束，不是硬约束。
- 若请求未传 `rest_days`，或传入空数组 `[]`，调度不会额外启用“周末/休息日优先留空”逻辑。
- 若请求传入了 `rest_days`，调度会先尽量使用其他工作日；只有在其他合法日期无法排下时，才会继续动用这些休息日。

#### DailyPartsConfig

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `day` | int | 是 | 工序天，从 `1` 开始 |
| `parts` | CoilPartConfig[] | 是 | 该工序天对应的零件需求 |

#### CoilPartConfig

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `part_id` | string | 是 | 零件 ID |
| `work_order` | string | 是 | 工令号 |
| `manufacturer` | string | 否 | 厂商名称，空值按 100% 效率处理 |
| `spec_prefix` | string | 否 | 规格前缀 |
| `coil_length` | float | 是 | 板长(mm) |
| `coil_width` | float | 是 | 板宽(mm) |
| `coil_thickness` | float | 是 | 板厚(mm) |
| `sheet_count` | int | 是 | 所需张数 |

### 请求示例

```json
{
  "total_days": 28,
  "rest_days": [6, 7, 13, 14, 20, 21, 27, 28],
  "lead_time_minutes": 2880,
  "daily_parts": [
    {
      "day": 3,
      "parts": [
        {
          "part_id": "P001",
          "work_order": "WO-A",
          "manufacturer": "默认配置",
          "spec_prefix": "HG",
          "coil_length": 6000,
          "coil_width": 2000,
          "coil_thickness": 12,
          "sheet_count": 20
        },
        {
          "part_id": "P002",
          "work_order": "WO-B",
          "manufacturer": "厂商A",
          "spec_prefix": "LG",
          "coil_length": 8000,
          "coil_width": 2500,
          "coil_thickness": 16,
          "sheet_count": 10
        }
      ]
    }
  ]
}
```

### 响应参数

#### CoilUncoilingResponseFull

| 字段 | 类型 | 说明 |
|------|------|------|
| `schedule` | DailyUncoilingPlan[] | 每日开卷计划 |
| `summary` | object | 汇总信息 |
| `pre_stock_requirements` | DailyPreStockRequirement[] | 按工序天统计的缺口清单 |

#### DailyUncoilingPlan

| 字段 | 类型 | 说明 |
|------|------|------|
| `day` | int | 排产日编号 |
| `work_orders` | WorkOrderUncoilingSummary[] | 当天按工令汇总的开卷信息 |
| `daily_total_weight_tons` | float | 当天总重量(吨) |
| `daily_total_sheets` | int | 当天总张数 |
| `daily_total_time_minutes` | float | 当天总开卷时间(分钟) |

#### WorkOrderUncoilingSummary

| 字段 | 类型 | 说明 |
|------|------|------|
| `work_order` | string | 工令号 |
| `total_weight_tons` | float | 该工令总重量(吨) |
| `sheet_count` | int | 该工令总张数 |
| `estimated_time_minutes` | float | 该工令预计开卷时间(分钟) |
| `specs` | CoilSpecSummary[] | 该工令下按规格汇总的张数与重量 |

#### CoilSpecSummary

| 字段 | 类型 | 说明 |
|------|------|------|
| `spec_prefix` | string \| null | 规格前缀 |
| `coil_length` | float | 长度(mm) |
| `coil_width` | float | 宽度(mm) |
| `coil_thickness` | float | 厚度(mm) |
| `sheet_count` | int | 张数 |
| `total_weight_tons` | float | 该规格总重量(吨) |

#### summary 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `total_weight_tons` | float | 全部总重量(吨) |
| `total_sheets` | int | 全部总张数 |
| `total_time_minutes` | float | 全部总开卷时间(分钟) |
| `total_days` | int | 输出计划总天数 |
| `avg_daily_time_minutes` | float | 平均每天开卷时间(分钟) |
| `avg_daily_weight_tons` | float | 平均每天重量(吨) |
| `night_shift_days` | int[] | 需要夜班的天编号 |
| `input_total_days` | int | 输入的 `total_days` |
| `pre_stock_shortage_sheets` | int | 缺口总张数 |
| `pre_stock_shortage_weight_tons` | float | 缺口总重量(吨) |
| `pre_stock_shortage_time_minutes` | float | 缺口总开卷时间(分钟) |
| `pre_stock_shortage_time_display` | string | 缺口时间展示文本 |

#### DailyPreStockRequirement

| 字段 | 类型 | 说明 |
|------|------|------|
| `day` | int | 工序天 |
| `specs` | PreStockSpec[] | 该天各规格缺口明细 |
| `daily_shortage_sheets` | int | 该天缺口总张数 |
| `daily_shortage_weight_tons` | float | 该天缺口总重量(吨) |
| `daily_shortage_time_minutes` | float | 该天缺口总开卷时间(分钟) |

#### PreStockSpec

| 字段 | 类型 | 说明 |
|------|------|------|
| `spec_prefix` | string \| null | 规格前缀 |
| `coil_length` | float | 长度(mm) |
| `coil_width` | float | 宽度(mm) |
| `coil_thickness` | float | 厚度(mm) |
| `shortage_sheets` | int | 缺口张数 |
| `shortage_weight_tons` | float | 缺口重量(吨) |
| `shortage_time_minutes` | float | 缺口对应开卷时间(分钟) |
| `work_orders` | string[] | 涉及工令 |
| `part_ids` | string[] | 涉及零件 ID |

### 响应示例

```json
{
  "schedule": [
    {
      "day": 1,
      "work_orders": [
        {
          "work_order": "WO-A",
          "total_weight_tons": 22.608,
          "sheet_count": 20,
          "estimated_time_minutes": 3.43,
          "specs": [
            {
              "spec_prefix": "HG",
              "coil_length": 6000,
              "coil_width": 2000,
              "coil_thickness": 12,
              "sheet_count": 20,
              "total_weight_tons": 22.608
            }
          ]
        }
      ],
      "daily_total_weight_tons": 22.608,
      "daily_total_sheets": 20,
      "daily_total_time_minutes": 3.43
    }
  ],
  "summary": {
    "total_weight_tons": 22.608,
    "total_sheets": 20,
    "total_time_minutes": 3.43,
    "total_days": 1,
    "avg_daily_time_minutes": 3.43,
    "avg_daily_weight_tons": 22.608,
    "night_shift_days": [],
    "input_total_days": 28,
    "pre_stock_shortage_sheets": 0,
    "pre_stock_shortage_weight_tons": 0,
    "pre_stock_shortage_time_minutes": 0,
    "pre_stock_shortage_time_display": ""
  },
  "pre_stock_requirements": []
}
```

### 缺口示例

```json
{
  "day": 3,
  "specs": [
    {
      "spec_prefix": "SPA-H",
      "coil_length": 2631,
      "coil_width": 1173,
      "coil_thickness": 2.0,
      "shortage_sheets": 1500,
      "shortage_weight_tons": 72.45,
      "shortage_time_minutes": 145.6,
      "work_orders": ["DFNB-2026-018-D"],
      "part_ids": ["SA002-QAB"]
    }
  ],
  "daily_shortage_sheets": 1500,
  "daily_shortage_weight_tons": 72.45,
  "daily_shortage_time_minutes": 145.6
}
```

### 错误响应

| HTTP 状态码 | 场景 |
|-------------|------|
| 400 | `daily_parts` 为空 |
| 400 | `total_days < 1` |
| 400 | `total_days < max(process_day)` |
| 400 | `scheduling_window_days < 1` |
| 400 | `scheduling_window_days < max(process_day)` |
| 422 | 字段缺失或类型错误 |
| 500 | 服务端内部错误 |
