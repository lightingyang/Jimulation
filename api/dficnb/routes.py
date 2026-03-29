# -*- coding: utf-8 -*-
"""寰宇集装箱仿真 + 钢卷开卷估算端点（DFICNB 域）"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from api._helpers import (
    logger,
    save_response_log,
    iter_configured_devices,
    run_simulation_with_sources,
    simulation_error_handler,
)
from api.models import (
    ContainerSimulationRequest,
    ContainerAnalysisResponse,
    DeviceWorkTime,
    TeamWorkTime,
    WorkOrderWorkTime,
    CoilUncoilingRequest,
    CoilUncoilingResponse,
    DailyUncoilingPlan,
)
from core.config_loader import load_equipment_config
from core.simulation import SimulationEngine
from core.results import ResultsManager
from core.coil_scheduling.scheduler import (
    DAY_SHIFT_MINUTES,
    build_uncoiling_tasks,
    build_uncoiling_batches,
    schedule_coil_tasks,
    format_time_display,
    build_daily_uncoiling_plan,
    build_pre_stock_requirements,
)

router = APIRouter(tags=["集装箱仿真"])


# ── 集装箱仿真私有函数 ───────────────────────────────────────


def _build_team_devices(equipment_config: Dict[str, Any]) -> Dict[str, List[str]]:
    team_devices: Dict[str, List[str]] = {}
    for team_name, _, device_name in iter_configured_devices(equipment_config):
        team_devices.setdefault(team_name, []).append(device_name)
    return team_devices


def _build_work_order_time_list(work_order_times: Dict[str, float]) -> List[WorkOrderWorkTime]:
    return [
        WorkOrderWorkTime(work_order=work_order, work_time=round(work_time, 2))
        for work_order, work_time in sorted(work_order_times.items())
    ]


def _build_team_results(
    team_devices: Dict[str, List[str]],
    device_work_times: Dict[str, float],
    device_work_order_times: Dict[str, Dict[str, float]]
) -> List[TeamWorkTime]:
    teams_result = []

    for team_name, device_names in team_devices.items():
        team_max_work_time = 0.0
        team_work_order_times: Dict[str, float] = {}
        devices_list = []

        for device_name in device_names:
            work_time = device_work_times.get(device_name, 0.0)
            team_max_work_time = max(team_max_work_time, work_time)

            if work_time > 0:
                devices_list.append(DeviceWorkTime(
                    device_name=device_name,
                    work_time=work_time,
                    work_order_times=_build_work_order_time_list(
                        device_work_order_times.get(device_name, {})
                    )
                ))

            for work_order, wo_time in device_work_order_times.get(device_name, {}).items():
                team_work_order_times[work_order] = max(team_work_order_times.get(work_order, 0.0), wo_time)

        if team_max_work_time > 0 and devices_list:
            teams_result.append(TeamWorkTime(
                name=team_name,
                work_time=round(team_max_work_time, 2),
                work_order_times=_build_work_order_time_list(team_work_order_times),
                devices=devices_list
            ))

    return sorted(teams_result, key=lambda x: x.work_time, reverse=True)


# ── 开卷端点本地模型 ─────────────────────────────────────────


class PreStockSpec(BaseModel):
    """某天某规格的库存缺口"""
    spec_prefix: Optional[str] = None
    coil_length: float = Field(..., description="长度(mm)")
    coil_width: float = Field(..., description="宽度(mm)")
    coil_thickness: float = Field(..., description="厚度(mm)")
    shortage_sheets: int = Field(..., description="缺口张数")
    shortage_weight_tons: float = Field(..., description="缺口重量(吨)")
    shortage_time_minutes: float = Field(..., description="缺口对应开卷时间(分钟)")
    work_orders: List[str] = Field(default_factory=list, description="涉及工令")
    part_ids: List[str] = Field(default_factory=list, description="涉及零件号")


class DailyPreStockRequirement(BaseModel):
    """按天统计的库存缺口"""
    day: int = Field(..., description="工序天")
    specs: List[PreStockSpec] = Field(default_factory=list, description="该天各规格缺口")
    daily_shortage_sheets: int = Field(..., description="该天缺口总张数")
    daily_shortage_weight_tons: float = Field(..., description="该天缺口总重量(吨)")
    daily_shortage_time_minutes: float = Field(..., description="该天缺口总开卷时间(分钟)")


class CoilUncoilingResponseFull(CoilUncoilingResponse):
    pre_stock_requirements: List[DailyPreStockRequirement] = Field(
        default_factory=list,
        description="按天统计的库存缺口清单",
    )


# ── 开卷请求验证 ─────────────────────────────────────────────


def _validate_uncoiling_request(request: CoilUncoilingRequest) -> None:
    if not request.daily_parts:
        raise HTTPException(status_code=400, detail="daily_parts 不能为空")

    if request.total_days < 1:
        raise HTTPException(status_code=400, detail="total_days 必须大于0")

    max_process_day = max(d.day for d in request.daily_parts)
    if request.total_days < max_process_day:
        raise HTTPException(
            status_code=400,
            detail=f"total_days ({request.total_days}) 不能小于最大工序天 ({max_process_day})",
        )

    if len(set(request.rest_days)) != len(request.rest_days):
        raise HTTPException(status_code=400, detail="rest_days 不能包含重复天数")
    for rest_day in request.rest_days:
        if rest_day < 1 or rest_day > request.total_days:
            raise HTTPException(
                status_code=400,
                detail=f"rest_days 中的天数 ({rest_day}) 必须位于 1 ~ total_days ({request.total_days}) 范围内",
            )

    window = request.scheduling_window_days
    if window is not None:
        if window < 1:
            raise HTTPException(status_code=400, detail="scheduling_window_days 必须大于0")
        if window < max_process_day:
            raise HTTPException(
                status_code=400,
                detail=f"scheduling_window_days ({window}) 不能小于最大工序天 ({max_process_day})",
            )


# ── 端点 ──────────────────────────────────────────────────────


@router.post("/container_simulation_status", response_model=ContainerAnalysisResponse)
@simulation_error_handler("集装箱仿真过程中发生未预期错误")
async def run_container_simulation_status(
    request: ContainerSimulationRequest,
    save_log: bool = Query(False, description="是否保存响应日志"),
):
    """运行集装箱建造仿真"""
    config_type = request.config_type

    if not request.products_config:
        raise HTTPException(status_code=400, detail="部件配置列表不能为空")

    if request.simulation_duration <= 0:
        raise HTTPException(status_code=400, detail="仿真时长必须大于0")

    simulator = SimulationEngine(simulation_duration=request.simulation_duration, config_type=config_type)
    equipment_config = load_equipment_config(simulator.config_path)
    team_devices = _build_team_devices(equipment_config)

    segment_to_work_order = {}

    run_simulation_with_sources(
        simulator=simulator,
        source_configs=request.products_config,
        id_attr='product_id',
        type_attr='product_type',
        work_order_map=segment_to_work_order,
        until=request.simulation_duration,
    )

    device_work_times, device_work_order_times = ResultsManager.collect_device_work_order_stats(
        devices=simulator.devices,
        segment_to_work_order=segment_to_work_order,
    )
    teams_result = _build_team_results(
        team_devices=team_devices,
        device_work_times=device_work_times,
        device_work_order_times=device_work_order_times
    )

    response_data = ContainerAnalysisResponse(
        simulation_duration=request.simulation_duration,
        teams=teams_result
    )

    if save_log:
        save_response_log("container_analysis_result", response_data)

    return response_data


@router.post("/coil_uncoiling_estimate", response_model=CoilUncoilingResponseFull)
@simulation_error_handler("钢卷开卷估算失败")
async def run_coil_uncoiling_estimate(
    request: CoilUncoilingRequest,
    save_log: bool = Query(False, description="是否保存响应日志"),
):
    """预处理线钢卷开卷时间估算"""
    _validate_uncoiling_request(request)
    window = request.scheduling_window_days if request.scheduling_window_days else request.total_days

    tasks = build_uncoiling_tasks(
        daily_parts=request.daily_parts,
        scheduling_window_days=request.scheduling_window_days,
        total_days=request.total_days,
        lead_time_minutes=request.lead_time_minutes,
    )
    batches = build_uncoiling_batches(tasks)
    day_tasks, scheduled_batches = schedule_coil_tasks(batches, window, request.rest_days)
    pre_stock_raw = build_pre_stock_requirements(scheduled_batches)
    pre_stock = [
        DailyPreStockRequirement(
            day=day_item["day"],
            specs=[PreStockSpec(**spec) for spec in day_item["specs"]],
            daily_shortage_sheets=day_item["daily_shortage_sheets"],
            daily_shortage_weight_tons=day_item["daily_shortage_weight_tons"],
            daily_shortage_time_minutes=day_item["daily_shortage_time_minutes"],
        )
        for day_item in pre_stock_raw
    ]

    schedule: list = []
    night_shift_days: list = []
    grand_total_weight = 0.0
    grand_total_sheets = 0
    grand_total_time = 0.0

    for sched_day in range(1, window + 1):
        task_slices = day_tasks.get(sched_day, [])
        daily_plan_dict, daily_weight, daily_sheets, daily_time = build_daily_uncoiling_plan(
            sched_day,
            task_slices,
        )

        if daily_time > DAY_SHIFT_MINUTES:
            night_shift_days.append(sched_day)

        grand_total_weight += daily_weight
        grand_total_sheets += daily_sheets
        grand_total_time += daily_time

        schedule.append(DailyUncoilingPlan(**daily_plan_dict))

    total_scheduling_days = len(schedule)
    total_shortage_sheets = sum(d.daily_shortage_sheets for d in pre_stock)
    total_shortage_time = round(sum(d.daily_shortage_time_minutes for d in pre_stock), 2)
    total_shortage_weight = round(sum(d.daily_shortage_weight_tons for d in pre_stock), 4)

    summary = {
        "total_weight_tons": round(grand_total_weight, 4),
        "total_sheets": grand_total_sheets,
        "total_time_minutes": round(grand_total_time, 2),
        "total_days": total_scheduling_days,
        "avg_daily_time_minutes": round(grand_total_time / total_scheduling_days, 2) if total_scheduling_days > 0 else 0.0,
        "avg_daily_weight_tons": round(grand_total_weight / total_scheduling_days, 4) if total_scheduling_days > 0 else 0.0,
        "night_shift_days": night_shift_days,
        "input_total_days": request.total_days,
        "pre_stock_shortage_sheets": total_shortage_sheets,
        "pre_stock_shortage_weight_tons": total_shortage_weight,
        "pre_stock_shortage_time_minutes": total_shortage_time,
        "pre_stock_shortage_time_display": format_time_display(total_shortage_time) if total_shortage_time > 0 else "",
    }

    response_data = CoilUncoilingResponseFull(
        schedule=schedule,
        summary=summary,
        pre_stock_requirements=pre_stock,
    )

    if save_log:
        save_response_log("coil_uncoiling_estimate", response_data)

    return response_data
