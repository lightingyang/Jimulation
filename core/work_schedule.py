# -*- coding: utf-8 -*-
"""工作日程管理模块 — 管理每日工作/休息周期和加班配置

将连续仿真升级为 24 小时制（工作 + 休息），使加班建模物理正确：
- 休息期间设备不接新任务
- 加班 = 延长设备工作窗口
- 加工时间不被缩放
"""

from typing import Dict, Optional, Tuple


class WorkSchedule:
    """工作日程管理器

    每天 = daily_work_time(工作) + rest_time(休息) = day_length
    默认: 480 + 960 = 1440 分钟 (24 小时)

    加班配置: {(device_type, day): extra_minutes}
    - device_type=None 表示全局加班（所有设备）
    """

    DAY_MINUTES = 1440  # 24 小时

    def __init__(self, daily_work_time: float = 480.0, rest_time: Optional[float] = None):
        self.daily_work_time = daily_work_time
        self.rest_time = rest_time if rest_time is not None else (self.DAY_MINUTES - daily_work_time)
        self.day_length = self.daily_work_time + self.rest_time
        # {(device_type, day): extra_minutes}  device_type=None 表示全局
        self._overtime: Dict[Tuple[Optional[str], int], float] = {}

    # ── 加班配置 ──

    def set_overtime(self, device_type: Optional[str], day: int, extra_minutes: float) -> None:
        """设置特定设备类型在特定天的加班分钟数

        Args:
            device_type: 设备类型(如 "QG")，None 表示所有设备
            day: 第几天(从 1 开始)
            extra_minutes: 加班分钟数(如 120 = 2小时)
        """
        if extra_minutes > 0:
            self._overtime[(device_type, day)] = extra_minutes

    def _get_overtime_minutes(self, device_type: Optional[str], day: int) -> float:
        """获取某设备类型在某天的加班分钟数（先查设备级，再查全局）"""
        if device_type is not None:
            specific = self._overtime.get((device_type, day))
            if specific is not None:
                return specific
        return self._overtime.get((None, day), 0.0)

    # ── 时间查询 ──

    def get_day(self, sim_time: float) -> int:
        """仿真时间 → 第几天(从 1 开始)"""
        if sim_time < 0:
            return 1
        return int(sim_time / self.day_length) + 1

    def get_day_start(self, day: int) -> float:
        """第 day 天的仿真起始时间"""
        return (day - 1) * self.day_length

    def get_work_window(self, day: int, device_type: Optional[str] = None) -> Tuple[float, float]:
        """某天某设备的工作时间窗口 [work_start, work_end)

        Returns:
            (work_start, work_end) — work_end 包含加班时间
        """
        day_start = self.get_day_start(day)
        work_start = day_start
        extra = self._get_overtime_minutes(device_type, day)
        work_end = day_start + self.daily_work_time + extra
        return work_start, work_end

    def is_work_time(self, device_type: Optional[str], sim_time: float) -> bool:
        """判断某设备类型在某仿真时刻是否处于工作时间"""
        day = self.get_day(sim_time)
        work_start, work_end = self.get_work_window(day, device_type)
        return work_start <= sim_time < work_end

    def next_work_start(self, sim_time: float) -> float:
        """从当前仿真时间算起，下一个工作日的开始时间

        如果当前处于工作时间内，返回当前时间（不需要等待）。
        """
        day = self.get_day(sim_time)
        _, work_end = self.get_work_window(day)  # 用全局窗口（最保守）
        if sim_time < work_end:
            # 当前还在某些设备的工作时间内，不需要等
            return sim_time
        # 等到下一天的开始
        return self.get_day_start(day + 1)

    # ── 时间换算 ──

    def sim_time_to_work_minutes(self, sim_time: float) -> float:
        """将仿真时间换算为工作分钟（扣除休息时间）

        注意：这里只计算默认工作时间（不含加班），
        因为加班时间对不同设备不同，无法统一换算。
        """
        if sim_time <= 0:
            return 0.0
        full_days = int(sim_time / self.day_length)
        remaining = sim_time - full_days * self.day_length
        work_minutes = full_days * self.daily_work_time
        # 剩余时间中的工作部分
        work_minutes += min(remaining, self.daily_work_time)
        return work_minutes

    def total_work_minutes(self, day: int, device_type: Optional[str] = None) -> float:
        """某天某设备类型的有效工作分钟数（含加班）"""
        extra = self._get_overtime_minutes(device_type, day)
        return self.daily_work_time + extra

