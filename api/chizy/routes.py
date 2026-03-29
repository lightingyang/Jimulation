# -*- coding: utf-8 -*-
"""重工管加工仿真端点（CHIZY 域）"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional, Tuple
import math

from api._helpers import (
    logger,
    save_response_log,
    iter_configured_devices,
    run_simulation_with_sources,
    simulation_error_handler,
)
from api.models import (
    PipeSimulationRequest,
    PipeAnalysisResponse,
    LineUtilization,
    DailySegmentInfo,
    OptimizationRecommendationResponse,
    Recommendation,
    SimulationComparison,
    DeviceAdjustment,
)
from core.config_loader import load_equipment_config
from core.simulation import SimulationEngine
from core.results import ResultsManager, calculate_utilization
from optimization.bottleneck_analyzer import BottleneckAnalyzer
from optimization.recommendation_engine import RecommendationEngine

router = APIRouter(tags=["管加工仿真"])


# ── 私有工具函数 ──────────────────────────────────────────────


def _build_device_maps(equipment_config: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """单次遍历构建 device→line_type 和 device→device_type 两个映射"""
    pipe_type_map: Dict[str, str] = {}
    device_type_map: Dict[str, str] = {}
    for line_type, device_type, device_name in iter_configured_devices(equipment_config):
        pipe_type_map[device_name] = line_type
        device_type_map[device_name] = device_type
    return pipe_type_map, device_type_map


def _sort_and_deduplicate_processes(
    process_source: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not process_source:
        return []

    result = sorted(process_source, key=lambda p: p['start_time'])

    if len(result) <= 1:
        return result

    seen = set()
    unique = []
    for p in result:
        key = (p['process_code'], p['start_time'], p['end_time'], p['device_id'])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _build_segment_detail(
    segment: Dict[str, Any],
    process_source: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    start_time = segment.get('process_start_time')
    end_time = segment.get('process_end_time')
    processes = _sort_and_deduplicate_processes(process_source)

    if (start_time is None or end_time is None) and processes:
        start_time = processes[0]['start_time']
        end_time = processes[-1]['end_time']

    if start_time is None or end_time is None:
        return None

    return {
        'start_time': round(float(start_time), 4),
        'end_time': round(float(end_time), 4),
        'processes': processes,
    }


# ── 响应构建子函数 ────────────────────────────────────────────


def _compute_daily_segments(
    max_completion_time: float,
    daily_work_time: float,
    device_names: List[str],
    daily_device_busy_times: Dict[int, Dict[str, float]],
    work_schedule=None,
) -> List[DailySegmentInfo]:
    """计算每日设备利用率

    Args:
        work_schedule: WorkSchedule 实例。提供时使用 24h 日程计算天数和利用率分母。
    """
    if work_schedule is not None:
        # 24h 制：天数 = sim_time / day_length
        total_days = max(1, math.ceil(max_completion_time / work_schedule.day_length))
    elif daily_work_time > 0 and max_completion_time > 0:
        total_days = max(1, math.ceil(max_completion_time / daily_work_time))
    else:
        total_days = 1

    daily_segments = []
    for day in range(1, total_days + 1):
        day_busy_times = daily_device_busy_times.get(day, {})
        device_utilization_map = {}
        # 利用率分母 = 该天有效工作时间（含加班）
        if work_schedule is not None:
            day_work_time = work_schedule.total_work_minutes(day)
        else:
            day_work_time = daily_work_time
        for device_name in device_names:
            busy_time = day_busy_times.get(device_name, 0.0)
            device_utilization_map[device_name] = round(calculate_utilization(busy_time, day_work_time), 2)
        daily_segments.append(DailySegmentInfo(day=day, device_utilization=device_utilization_map))
    return daily_segments


def _compute_utilization_analysis(
    all_device_utilizations: Dict[str, float],
    config_path: str,
    products_config: List[Any],
) -> List[LineUtilization]:
    """计算产线利用率分析"""
    equipment_config = load_equipment_config(config_path)
    device_pipe_type_map, device_to_device_type_map = _build_device_maps(equipment_config)
    line_types = {
        getattr(pipe_config, 'product_type', 'standard')
        for pipe_config in products_config
    }

    line_process_avg_utilizations: Dict[str, Dict[str, List[float]]] = {}
    line_device_utilization_totals: Dict[str, List[float]] = {}
    for device_name, utilization in all_device_utilizations.items():
        pipe_type = device_pipe_type_map.get(device_name)
        device_type = device_to_device_type_map.get(device_name)
        if not pipe_type:
            continue

        line_device_utilization_totals.setdefault(pipe_type, []).append(utilization)
        if device_type:
            line_process_avg_utilizations.setdefault(pipe_type, {}).setdefault(device_type, []).append(utilization)

    for pipe_type, device_types in line_process_avg_utilizations.items():
        for device_type, util_list in device_types.items():
            device_types[device_type] = round(sum(util_list) / len(util_list), 2)

    utilization_analysis = []
    for pipe_type in line_types:
        line_device_utils = line_device_utilization_totals.get(pipe_type, [])
        overall_utilization = round(sum(line_device_utils) / len(line_device_utils), 2) if line_device_utils else 0.0
        process_utilization = line_process_avg_utilizations.get(pipe_type) or None

        utilization_analysis.append(LineUtilization(
            line_name=pipe_type,
            overall_utilization=overall_utilization,
            process_utilization=process_utilization
        ))
    return utilization_analysis


def _build_simulation_details(
    product_results: Dict[str, Any],
    segment_processes_map: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """构建已完成管件的仿真详情"""
    simulation_details = {}
    for pipe_id, prod in product_results.items():
        if prod.get('end_time') is None:
            continue

        seg_map = {}
        for seg in prod.get('segments', []):
            segment_id = seg.get('segment_id')
            if not segment_id:
                continue
            segment_detail = _build_segment_detail(
                seg,
                segment_processes_map.get(segment_id, []),
            )
            if segment_detail is None:
                continue
            seg_map[segment_id] = segment_detail

        simulation_details[pipe_id] = {
            'start_time': round(float(prod.get('start_time', 0.0) or 0.0), 4),
            'end_time': round(float(prod.get('end_time', 0.0) or 0.0), 4),
            'segments': seg_map
        }
    return simulation_details


# ── 核心仿真构建 ──────────────────────────────────────────────


def _build_pipe_analysis_response(
    request: PipeSimulationRequest,
    save_response_log_flag: bool = True,
    include_daily_segments: bool = True,
    include_utilization_analysis: bool = True,
    include_simulation_details: bool = True,
    enable_queue_sampling: bool = False,
) -> Tuple[PipeAnalysisResponse, SimulationEngine]:
    if not request.products_config:
        raise HTTPException(status_code=400, detail="配置列表不能为空")
    if request.simulation_duration <= 0:
        raise HTTPException(status_code=400, detail="仿真时长必须大于0")

    simulator = SimulationEngine(
        simulation_duration=request.simulation_duration,
        config_type=request.config_type,
        daily_work_time=request.daily_work_time
    )
    simulator.set_queue_sampling_enabled(enable_queue_sampling)

    if request.device_adjustments:
        adjustments = [adj.model_dump() for adj in request.device_adjustments]
        simulator.set_device_adjustments(adjustments)

    run_simulation_with_sources(
        simulator=simulator,
        source_configs=request.products_config,
        id_attr='product_id',
        type_attr='product_type',
        until=request.simulation_duration,
    )

    results = simulator.get_results(
        include_device_statistics=False,
        include_work_logs=False,
    )

    work_schedule = simulator.work_schedule

    segment_processes_map: Dict[str, List[Dict[str, Any]]] = {}
    daily_device_busy_times: Dict[int, Dict[str, float]] = {}
    all_device_utilizations: Dict[str, float] = {}
    if include_daily_segments or include_utilization_analysis or include_simulation_details:
        segment_processes_map, daily_device_busy_times, all_device_utilizations = ResultsManager.collect_device_metrics(
            simulator.devices,
            request.daily_work_time,
            include_segment_processes=include_simulation_details,
            include_daily_busy_times=include_daily_segments,
            include_device_utilization=include_utilization_analysis,
            work_schedule=work_schedule,
        )

    completion_times = [
        float(prod['end_time']) for prod in results.get('product_results', {}).values()
        if prod.get('end_time') is not None
    ]
    max_completion_time = max(completion_times) if completion_times else 0.0
    # 将 24h 制仿真时间换算为工作分钟
    effective_time_work_minutes = work_schedule.sim_time_to_work_minutes(max_completion_time)

    simulation_summary = {
        "simulation_time": results["simulation_time"],
        "effective_time": round(effective_time_work_minutes, 2),
        "daily_work_time": request.daily_work_time,
        "total_pipes": len(request.products_config),
        "progress": {
            "total_products": results["total_products"],
            "completed_products": results["completed_products"],
            "failed_products": results["failed_products"]
        }
    }

    daily_segments = (
        _compute_daily_segments(max_completion_time, request.daily_work_time,
                                list(simulator.devices.keys()), daily_device_busy_times,
                                work_schedule=work_schedule)
        if include_daily_segments else []
    )

    utilization_analysis = (
        _compute_utilization_analysis(all_device_utilizations, simulator.config_path, request.products_config)
        if include_utilization_analysis else []
    )

    simulation_details = (
        _build_simulation_details(results.get('product_results', {}), segment_processes_map)
        if include_simulation_details else {}
    )

    response_data = PipeAnalysisResponse(
        simulation_summary=simulation_summary,
        total_device_utilization=all_device_utilizations,
        utilization_analysis=utilization_analysis,
        daily_segments=daily_segments,
        simulation_details=simulation_details
    )

    if save_response_log_flag:
        save_response_log("pipe_analysis_result", response_data)

    return response_data, simulator


def _calculate_comparison(baseline, optimized) -> SimulationComparison:
    baseline_time = baseline.simulation_summary['effective_time']
    optimized_time = optimized.simulation_summary['effective_time']

    time_reduction = baseline_time - optimized_time
    time_reduction_pct = (time_reduction / baseline_time * 100) if baseline_time > 0 else 0

    return SimulationComparison(
        baseline_completion_time=round(baseline_time, 2),
        optimized_completion_time=round(optimized_time, 2),
        time_reduction_minutes=round(time_reduction, 2),
        time_reduction_percentage=round(time_reduction_pct, 2)
    )


# ── 端点 ──────────────────────────────────────────────────────


@router.post("/pipe_simulation_status", response_model=PipeAnalysisResponse)
@simulation_error_handler("管道分析仿真过程中发生未预期错误")
async def run_pipe_simulation_status(
    request: PipeSimulationRequest,
    save_log: bool = Query(False, description="是否保存响应日志"),
):
    """运行管加工生产仿真状态分析"""
    response_data, _ = _build_pipe_analysis_response(request, save_response_log_flag=save_log)
    return response_data


@router.post("/pipe_simulation_optimization", response_model=OptimizationRecommendationResponse)
@simulation_error_handler("优化分析失败")
async def run_pipe_simulation_optimization(
    request: PipeSimulationRequest,
    save_log: bool = Query(False, description="是否保存响应日志"),
):
    """运行优化分析并生成建议"""
    baseline_response, baseline_simulator = _build_pipe_analysis_response(
        request,
        save_response_log_flag=False,
        include_daily_segments=True,
        include_utilization_analysis=False,
        include_simulation_details=False,
        enable_queue_sampling=True,
    )

    analyzer = BottleneckAnalyzer(request.daily_work_time, work_schedule=baseline_simulator.work_schedule)
    bottlenecks = analyzer.analyze_bottlenecks(
        baseline_response.daily_segments,
        baseline_simulator.devices
    )

    engine = RecommendationEngine(request.daily_work_time)
    recommendations = engine.generate_recommendations(bottlenecks)

    if not recommendations:
        response_data = OptimizationRecommendationResponse(
            recommendations=[],
            comparison=SimulationComparison(
                baseline_completion_time=baseline_response.simulation_summary['effective_time'],
                optimized_completion_time=baseline_response.simulation_summary['effective_time'],
                time_reduction_minutes=0.0,
                time_reduction_percentage=0.0
            ),
            baseline_summary=baseline_response.simulation_summary,
            optimized_summary=baseline_response.simulation_summary,
            device_adjustments=[]
        )
        if save_log:
            save_response_log("pipe_optimization_result", response_data)
        return response_data

    adjustments = engine.convert_to_device_adjustments(
        recommendations,
        baseline_simulator.original_device_counts,
    )

    optimized_request = PipeSimulationRequest(
        config_type=request.config_type,
        products_config=request.products_config,
        simulation_duration=request.simulation_duration,
        daily_work_time=request.daily_work_time,
        device_adjustments=[DeviceAdjustment(**adj) for adj in adjustments]
    )
    optimized_response, _ = _build_pipe_analysis_response(
        optimized_request,
        save_response_log_flag=False,
        include_daily_segments=False,
        include_utilization_analysis=False,
        include_simulation_details=False,
        enable_queue_sampling=False,
    )

    response_data = OptimizationRecommendationResponse(
        recommendations=[Recommendation(**r) for r in recommendations],
        comparison=_calculate_comparison(baseline_response, optimized_response),
        baseline_summary=baseline_response.simulation_summary,
        optimized_summary=optimized_response.simulation_summary,
        device_adjustments=[DeviceAdjustment(**adj) for adj in adjustments]
    )
    if save_log:
        save_response_log("pipe_optimization_result", response_data)
    return response_data
