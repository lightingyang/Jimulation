# -*- coding: utf-8 -*-
"""工艺流程编排模块 — 统一装配机制版

父件（有 children）在第一个工序前等待所有子件完成，然后正常处理工序。
"""

import simpy
import logging
from typing import Dict

from .models import Segment


class ProcessFlowManager:
    """工艺流程编排器

    职责:
    - 编排 segment 的工序执行顺序
    - 父件在装配工序处等待子件完成（AssemblyCoordinator）
    - 子件完成后通知父节点
    - 在休息时间阻止设备接新任务（WorkSchedule 门控）

    委托:
    - 设备选择 → DevicePool
    - 工序计算 → ProcessCalculator
    - 装配协调 → AssemblyCoordinator
    - 工作/休息周期 → WorkSchedule
    """

    def __init__(self, env: simpy.Environment, device_pool, process_calculator,
                 assembly_coordinator, work_schedule=None):
        self.env = env
        self.device_pool = device_pool
        self.process_calculator = process_calculator
        self.assembly_coordinator = assembly_coordinator
        self.work_schedule = work_schedule
        self.logger = logging.getLogger('ProcessFlowManager')
        self.logger.setLevel(logging.WARNING)

        # 预计算缓存（在 _ensure_prepared() 中填充）
        self._total_times: Dict[str, float] = {}
        self._prepared: bool = False

    # === 便捷属性（向后兼容） ===

    @property
    def devices(self):
        return self.device_pool.devices

    # === 预计算 ===

    def _ensure_prepared(self) -> None:
        """懒初始化：缓存每个工序的 total_time"""
        if self._prepared:
            return
        for name, proc in self.process_calculator.process_definitions.items():
            self._total_times[name] = proc.duration + proc.setup_time + proc.cooling_time
        self._prepared = True

    # === 核心编排 ===

    def process_segment(self, segment: Segment):
        """主 segment 处理生成器"""
        self._ensure_prepared()
        segment.transition_to('in_progress')

        total_times = self._total_times

        try:
            children = self.assembly_coordinator.get_children(
                segment.product_id, segment.segment_id
            )
            waited_for_children = False

            for process_code in segment.process_codes:
                total_time = total_times.get(process_code)
                if total_time is None:
                    total_time = self.process_calculator.get_process_duration(
                        process_code
                    )

                # 有子件 → 在第一个工序前等待所有子件完成
                if children and not waited_for_children:
                    yield from self.assembly_coordinator.wait_for_children(
                        segment.product_id, segment.segment_id
                    )
                    waited_for_children = True

                # 统一走普通工序处理
                device, device_req = yield from self._acquire_and_process(
                    process_code, segment, total_time
                )

                if not device:
                    yield self.env.timeout(0.1)
                    device, device_req = yield from self._acquire_and_process(
                        process_code, segment, total_time
                    )
                    if not device:
                        self.logger.error(
                            f"时间 {self.env.now:.1f}: 工序 {process_code} "
                            f"(segment={segment.segment_id}, product={segment.product_id}) 无法获取设备"
                        )
                        segment.transition_to('failed')
                        segment.end_time = self.env.now
                        return

                # 释放设备
                device.release_request(device_req)
                self.device_pool.sample_device_queue(device)

        except Exception as e:
            self.logger.error(f"时间 {self.env.now:.1f}: 工序处理错误 - {e}")
            if segment.status != 'failed':
                segment.transition_to('failed')
            segment.end_time = self.env.now
            return

        segment.end_time = self.env.now
        segment.transition_to('completed')

        # 通知父节点：子件已完成
        self.assembly_coordinator.notify_child_complete(
            segment.product_id, segment.segment_id
        )

    def _wait_for_work_time(self, device_type: str) -> None:
        """等待到工作时间（如果当前处于休息时间）"""
        if self.work_schedule is None:
            return
        while not self.work_schedule.is_work_time(device_type, self.env.now):
            next_start = self.work_schedule.next_work_start(self.env.now)
            if next_start <= self.env.now:
                break
            yield self.env.timeout(next_start - self.env.now)

    def _acquire_and_process(self, process_name: str, segment: Segment,
                             process_duration: float):
        """获取设备并执行加工。返回 (device, request)。不释放设备。"""
        ws = self.work_schedule  # 局部变量避免重复属性查找

        # 等待工作时间
        if ws is not None:
            yield from self._wait_for_work_time(process_name)

        device = self.device_pool.select_best_device(process_name)
        if not device:
            # 可能所有设备都在休息，等到下一工作日再试
            if ws is not None:
                next_start = ws.next_work_start(self.env.now)
                if next_start > self.env.now:
                    yield self.env.timeout(next_start - self.env.now)
                    device = self.device_pool.select_best_device(process_name)
            if not device:
                return None, None

        device_req = device.resource.request(priority=segment.priority)
        self.device_pool.sample_device_queue(device)
        yield device_req
        self.device_pool.sample_device_queue(device)

        # 门控：SimPy 可能在休息时自动授权排队请求，此处再次检查
        if ws is not None and not ws.is_work_time(process_name, self.env.now):
            while True:
                device.release_request(device_req)
                next_start = ws.next_work_start(self.env.now)
                if next_start <= self.env.now:
                    break
                yield self.env.timeout(next_start - self.env.now)
                device_req = device.resource.request(priority=segment.priority)
                yield device_req
                if ws.is_work_time(process_name, self.env.now):
                    break

        start_time = self.env.now
        device.mark_process_start(start_time)

        actual = self.process_calculator.calculate_duration(
            process_name=process_name,
            base_duration=process_duration,
        )

        yield self.env.timeout(actual)

        device.mark_process_end(duration=actual, end_time=self.env.now)
        device.append_process_logs(
            event=f"完成工序 {process_name}",
            segment_ids=[segment.segment_id],
            start_time=start_time,
            end_time=self.env.now,
            duration=actual,
            process_code=process_name,
            event_type="process_completed",
            product_id=segment.product_id,
        )

        if segment.start_time is None or start_time < segment.start_time:
            segment.start_time = start_time

        return device, device_req
