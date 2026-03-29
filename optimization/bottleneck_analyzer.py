# -*- coding: utf-8 -*-
"""瓶颈分析模块"""

import re
from typing import List, Dict, Any


class BottleneckAnalyzer:
    """瓶颈分析器，识别设备瓶颈"""

    def __init__(self, daily_work_time: float, work_schedule=None):
        self.daily_work_time = daily_work_time
        self.work_schedule = work_schedule
        self.high_util_threshold = 85.0
        self.critical_util_threshold = 95.0

    def analyze_bottlenecks(
        self,
        daily_segments: List[Any],
        devices: Dict[str, Any]
    ) -> List[Dict]:
        """识别瓶颈设备"""
        bottlenecks = []

        for day_info in daily_segments:
            day = day_info.day
            for device_name, utilization in day_info.device_utilization.items():
                if utilization >= self.high_util_threshold:
                    device = devices.get(device_name)
                    avg_queue = self._calculate_avg_queue(device, day) if device else 0.0

                    bottlenecks.append({
                        'device_name': device_name,
                        'device_type': self._extract_device_type(device_name),
                        'day': day,
                        'utilization': utilization,
                        'avg_queue_length': avg_queue,
                        'severity': self._calculate_severity(utilization, avg_queue)
                    })

        return sorted(bottlenecks, key=lambda x: x['severity'], reverse=True)

    def _calculate_avg_queue(self, device, day: int) -> float:
        """计算特定天的平均队列长度"""
        if not hasattr(device, 'queue_samples') or not device.queue_samples:
            return 0.0

        if self.work_schedule is not None:
            day_start, day_end = self.work_schedule.get_work_window(day)
        else:
            day_start = (day - 1) * self.daily_work_time
            day_end = day * self.daily_work_time

        weighted_total = 0.0
        current_time = day_start
        current_queue = 0.0

        for sample_time, queue_length in device.queue_samples:
            if sample_time < day_start:
                current_queue = queue_length
                continue
            if sample_time >= day_end:
                break
            if sample_time > current_time:
                weighted_total += current_queue * (sample_time - current_time)
                current_time = sample_time
            current_queue = queue_length

        if current_time < day_end:
            weighted_total += current_queue * (day_end - current_time)

        return weighted_total / self.daily_work_time if self.daily_work_time > 0 else 0.0

    def _calculate_severity(self, utilization: float, queue_length: float) -> float:
        """计算瓶颈严重程度"""
        return (utilization * 0.7) + (min(queue_length * 10, 30) * 0.3)

    def _extract_device_type(self, device_name: str) -> str:
        """从设备名提取设备类型"""
        return re.sub(r'(?:_temp)?\d+$', '', device_name)
