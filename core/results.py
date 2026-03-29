# -*- coding: utf-8 -*-
"""仿真结果管理模块"""

from typing import Dict, List, Any, Tuple, Optional
from .models import PipeProduct


def calculate_utilization(busy_time: float, effective_time: float, as_percent: bool = True) -> float:
    """统一利用率计算

    Args:
        busy_time: 设备忙碌时间
        effective_time: 有效时间跨度
        as_percent: True 返回百分比 (0~100)，False 返回比率 (0~1)

    Returns:
        利用率值，已 clamp 到上限
    """
    if effective_time <= 0:
        return 0.0
    ratio = busy_time / effective_time
    if as_percent:
        return min(ratio * 100, 100.0)
    return min(ratio, 1.0)


class ResultsManager:
    """结果管理器"""

    @staticmethod
    def get_simulation_results(
        products: List[PipeProduct],
        devices: Dict[str, Any],
        simulation_time: float,
        include_device_statistics: bool = True,
        include_work_logs: bool = True,
        include_product_results: bool = True,
    ) -> Dict[str, Any]:
        status_counts = {}
        for product in products:
            status = product.status
            status_counts[status] = status_counts.get(status, 0) + 1

        device_statistics = []
        avg_util = max_util = min_util = 0.0
        if include_device_statistics:
            for device_name, device in devices.items():
                stats = device.get_stats()
                device_statistics.append({
                    'device_id': device_name,
                    'name': device_name,
                    'utilization': stats['utilization'],
                    'processed_count': stats['processed_count'],
                    'total_busy_time': stats['total_busy_time'],
                    'average_process_time': stats['average_process_time']
                })

            working_devices = [d for d in device_statistics if d['processed_count'] > 0]
            if working_devices:
                avg_util = sum(d['utilization'] for d in working_devices) / len(working_devices)
                max_util = max(d['utilization'] for d in working_devices)
                min_util = min(d['utilization'] for d in working_devices)

        work_logs = []
        if include_work_logs:
            for device_name, device in devices.items():
                for log in getattr(device, 'logs', ()):
                    work_logs.append({
                        'timestamp': getattr(log, 'timestamp', 0),
                        'segment_id': getattr(log, 'segment_id', ''),
                        'device_name': device_name,
                        'action': getattr(log, 'event', ''),
                        'event_type': getattr(log, 'event_type', None),
                        'process_code': getattr(log, 'process_code', None),
                        'product_id': getattr(log, 'product_id', None),
                        'start_time': getattr(log, 'start_time', 0),
                        'end_time': getattr(log, 'end_time', 0),
                        'duration': getattr(log, 'duration', 0)
                    })

        product_results = {}
        if include_product_results:
            for product in products:
                segments_info = []
                for segment in product.segments:
                    segments_info.append({
                        'segment_id': segment.segment_id,
                        'segment_type': segment.segment_type,
                        'process_start_time': segment.start_time,
                        'process_end_time': segment.end_time,
                        'status': segment.status
                    })

                product_results[product.product_id] = {
                    'product_id': product.product_id,
                    'product_type': product.product_type,
                    'pipe_type': product.pipe_type,
                    'status': product.status,
                    'priority': product.priority,
                    'start_time': product.start_time,
                    'end_time': product.end_time,
                    'segments': segments_info
                }

        return {
            'simulation_time': simulation_time,
            'total_devices': len(devices),
            'total_products': len(products),
            'completed_products': status_counts.get('completed', 0),
            'failed_products': status_counts.get('failed', 0),
            'status_counts': status_counts,
            'device_statistics': device_statistics,
            'utilization_summary': {
                'average': {'value': round(avg_util, 2)},
                'max': {'value': round(max_util, 2)},
                'min': {'value': round(min_util, 2)}
            },
            'product_results': product_results,
            'work_logs': work_logs
        }

    # === 从 API 层迁入的指标收集方法 ===

    @staticmethod
    def collect_device_metrics(
        devices: Dict[str, Any],
        daily_work_time: float,
        include_segment_processes: bool = True,
        include_daily_busy_times: bool = True,
        include_device_utilization: bool = True,
        work_schedule=None,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[int, Dict[str, float]], Dict[str, float]]:
        """收集设备运行指标

        Args:
            work_schedule: WorkSchedule 实例。提供时使用 24h 日程计算天边界，
                          否则退化为旧逻辑（连续分割）。

        Returns:
            (segment_processes_map, daily_device_busy_times, all_device_utilizations)
        """
        segment_processes_map: Dict[str, List[Dict[str, Any]]] = {}
        daily_device_busy_times: Dict[int, Dict[str, float]] = {}
        all_device_utilizations: Dict[str, float] = {}
        do_daily = include_daily_busy_times and daily_work_time > 0

        # 预计算天边界缓存，避免每条日志重复计算
        _day_boundary_cache: Dict[int, float] = {}

        def _get_day_and_end(sim_time: float):
            """返回 (day, day_end_time)，使用缓存避免重复计算"""
            if work_schedule is not None:
                day = work_schedule.get_day(sim_time)
            else:
                day = int(sim_time / daily_work_time) + 1
            day_end = _day_boundary_cache.get(day)
            if day_end is None:
                if work_schedule is not None:
                    day_end = work_schedule.get_day_start(day + 1)
                else:
                    day_end = day * daily_work_time
                _day_boundary_cache[day] = day_end
            return day, day_end

        for device_name, device in devices.items():
            total_busy_time = 0.0
            min_start = float('inf')
            max_end = 0.0
            has_logs = False

            # 预计算每个设备的回退工序码（去除末尾数字）
            if include_segment_processes:
                fallback_code = device_name.rstrip("0123456789")

            for log in device.logs:
                start_time = log.start_time
                end_time = log.end_time
                if end_time <= start_time:
                    continue

                if include_device_utilization:
                    has_logs = True
                    total_busy_time += end_time - start_time
                    if start_time < min_start:
                        min_start = start_time
                    if end_time > max_end:
                        max_end = end_time

                if include_segment_processes:
                    segment_id = log.segment_id
                    if segment_id:
                        process_code = getattr(log, 'process_code', None) or fallback_code
                        segment_processes_map.setdefault(segment_id, []).append({
                            "process_code": process_code,
                            "device_id": device_name,
                            "start_time": round(start_time, 4),
                            "end_time": round(end_time, 4),
                        })

                if do_daily:
                    current_time = start_time
                    while current_time < end_time:
                        day, day_end_time = _get_day_and_end(current_time)
                        overlap_end = min(end_time, day_end_time)
                        day_dict = daily_device_busy_times.get(day)
                        if day_dict is None:
                            day_dict = {}
                            daily_device_busy_times[day] = day_dict
                        day_dict[device_name] = (
                            day_dict.get(device_name, 0.0)
                            + (overlap_end - current_time)
                        )
                        current_time = overlap_end

            if include_device_utilization:
                if has_logs:
                    effective = max_end - min_start
                    all_device_utilizations[device_name] = round(
                        calculate_utilization(total_busy_time, effective), 2
                    )
                else:
                    all_device_utilizations[device_name] = 0.0

        return segment_processes_map, daily_device_busy_times, all_device_utilizations

    @staticmethod
    def collect_device_work_order_stats(
        devices: Dict[str, Any],
        segment_to_work_order: Dict[str, str],
        unknown_label: str = "未知工令",
    ) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
        """收集设备工令维度统计（从 dficnb/routes.py _compute_device_work_order_stats 迁入）

        Returns:
            (device_work_times, device_work_order_times)
        """
        device_work_times: Dict[str, float] = {}
        device_work_order_times: Dict[str, Dict[str, float]] = {}

        for device_name, device in devices.items():
            total_busy_time = 0.0
            work_order_times: Dict[str, float] = {}

            for log in device.logs:
                start_time = log.start_time
                end_time = log.end_time
                if end_time <= start_time:
                    continue

                work_time = end_time - start_time
                total_busy_time += work_time

                segment_id = log.segment_id
                if segment_id:
                    work_order = segment_to_work_order.get(segment_id, unknown_label) or unknown_label
                    work_order_times[work_order] = work_order_times.get(work_order, 0.0) + work_time

            device_work_times[device_name] = round(total_busy_time, 2)
            device_work_order_times[device_name] = work_order_times

        return device_work_times, device_work_order_times
