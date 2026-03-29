# -*- coding: utf-8 -*-
"""推荐引擎模块"""

from typing import List, Dict, Any


class RecommendationEngine:
    """推荐引擎，生成优化建议"""

    def __init__(self, daily_work_time: float):
        self.daily_work_time = daily_work_time

    def generate_recommendations(self, bottlenecks: List[Dict]) -> List[Dict]:
        """根据瓶颈生成优化建议"""
        recommendations = []
        device_day_map = {}

        for bottleneck in bottlenecks[:5]:
            device_type = bottleneck['device_type']
            day = bottleneck['day']
            utilization = bottleneck['utilization']
            queue_length = bottleneck['avg_queue_length']

            key = (device_type, day)
            if key in device_day_map:
                continue

            if utilization >= 95:
                recommendations.append({
                    'device_type': device_type,
                    'recommendation_type': 'add_devices',
                    'target_days': [day],
                    'additional_devices': 1,
                    'reason': f"关键瓶颈，利用率 {utilization:.1f}%"
                })
            elif utilization >= 85 and queue_length > 5:
                recommendations.append({
                    'device_type': device_type,
                    'recommendation_type': 'add_devices',
                    'target_days': [day],
                    'additional_devices': 1,
                    'reason': f"高利用率 ({utilization:.1f}%) 且堵料严重"
                })
            elif utilization >= 85:
                recommendations.append({
                    'device_type': device_type,
                    'recommendation_type': 'extend_work_hours',
                    'target_days': [day],
                    'additional_hours': 60.0,
                    'reason': f"高利用率 ({utilization:.1f}%)，建议延长工时"
                })

            device_day_map[key] = True

        return self._consolidate_recommendations(recommendations)

    def _consolidate_recommendations(self, recommendations: List[Dict]) -> List[Dict]:
        """合并连续天数的相同建议"""
        consolidated = {}
        target_days_sets: Dict[tuple, set] = {}

        for rec in recommendations:
            key = (rec['device_type'], rec['recommendation_type'])
            if key not in consolidated:
                consolidated[key] = rec.copy()
                target_days_sets[key] = set(rec['target_days'])
            else:
                target_days_sets[key].update(rec['target_days'])

        for key, rec in consolidated.items():
            rec['target_days'] = sorted(target_days_sets[key])

        return list(consolidated.values())

    def convert_to_device_adjustments(
        self,
        recommendations: List[Dict],
        original_device_counts: Dict[str, int]
    ) -> List[Dict]:
        """将建议转换为DeviceAdjustment格式"""
        adjustments = []

        for rec in recommendations:
            device_type = rec['device_type']
            days = rec['target_days']

            start_time = (min(days) - 1) * self.daily_work_time
            end_time = max(days) * self.daily_work_time

            if rec['recommendation_type'] == 'add_devices':
                original_count = original_device_counts.get(device_type, 1)
                adjustments.append({
                    'device_name': device_type,
                    'start_time': start_time,
                    'end_time': end_time,
                    'count': original_count + rec['additional_devices']
                })
            elif rec['recommendation_type'] == 'extend_work_hours':
                extended_time = self.daily_work_time + rec['additional_hours']
                adjustments.append({
                    'device_name': device_type,
                    'start_time': start_time,
                    'end_time': end_time,
                    'adjusted_time': extended_time
                })

        return adjustments
