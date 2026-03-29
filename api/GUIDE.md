# api/ 目录指引

> AI 或新开发者进入此目录时请首先阅读本文件。

## 架构概览

本目录按**公司/业务域**拆分 API 端点，支持按需裁剪部署：

```
api/
├── GUIDE.md        ← 你正在读的这个文件
├── __init__.py     ← FastAPI 应用工厂 + 动态路由发现 + 内置系统端点(health, config_info)
├── models.py       ← 所有 Pydantic 请求/响应模型（各域共用）
├── _helpers.py     ← 跨域共用的工具函数（仅放被多个域同时使用的）
├── chizy/          ← 重工管加工域（可整个删除以裁剪）
│   └── routes.py   ←   POST /pipe_simulation_status, /pipe_simulation_optimization
└── dficnb/         ← 寰宇集装箱+开卷域（可整个删除以裁剪）
    └── routes.py   ←   POST /container_simulation_status, /coil_uncoiling_estimate
                         开卷排产的计算逻辑在 core/coil_scheduling/
```

## 如何裁剪功能

部署只给某一家公司用的版本时，直接**删除对应文件夹**即可：

- 只保留重工功能：删除 `dficnb/` 文件夹
- 只保留寰宇功能：删除 `chizy/` 文件夹
- 全功能版本：两个文件夹都保留

`__init__.py` 中的 `_discover_routers()` 会自动扫描子目录，缺失的域静默跳过。

## 如何新增公司域

1. 在 `api/` 下创建新目录，如 `api/newco/`
2. 添加 `__init__.py`（空文件）和 `routes.py`
3. 在 `routes.py` 中定义 `router = APIRouter(tags=["..."])`，挂载端点
4. 重启服务即可自动发现

## 依赖关系

```
域模块 (chizy/, dficnb/)
  ├── api._helpers      公共工具函数
  ├── api.models         数据模型
  ├── core.simulation    仿真引擎（管加工/集装箱）
  ├── core.coil_scheduling  开卷排产引擎（dficnb 专用）
  └── optimization.*     优化分析（不可修改）
```

**规则**：域模块之间不可互相 import。公共逻辑放 `_helpers.py`，只放被多个域同时使用的。
