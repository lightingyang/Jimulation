# -*- coding: utf-8 -*-
"""设备池管理模块

负责设备池的初始化、设备选择、活跃状态检查。
从 ProcessFlowManager 中提取的设备管理逻辑。
"""

import logging
import simpy
from typing import Dict, List, Any, Optional

from .base_device import BaseDevice


class DevicePool:
    """设备池管理器"""

    def __init__(self, env: simpy.Environment):
        self.env = env
        self.device_pools: Dict[str, List[BaseDevice]] = {}
        self.devices: Dict[str, BaseDevice] = {}
        self.device_disable_schedule: Dict[str, List[Dict]] = {}
        self.adjustments_by_device: Dict[str, List[Dict]] = {}
        self.daily_work_time: float = 480.0
        self.original_device_counts: Dict[str, int] = {}
        self.queue_sampling_enabled: bool = False
        self.logger = logging.getLogger('DevicePool')

    def initialize(self, devices: Dict[str, BaseDevice]) -> None:
        """从设备字典构建设备池"""
        self.devices = devices
        self.device_pools.clear()

        for device in self.devices.values():
            if hasattr(device, 'process_codes') and device.process_codes:
                for process_code in device.process_codes:
                    if process_code not in self.device_pools:
                        self.device_pools[process_code] = []
                    self.device_pools[process_code].append(device)

        for process_code in self.device_pools:
            self.device_pools[process_code].sort(key=lambda d: (d.historical_count, d.name))

        self.logger.debug(f"设备池初始化完成，共 {len(self.device_pools)} 个工序池")

    def add_temp_devices(self, temp_devices: Dict[str, BaseDevice]) -> None:
        """注册临时设备到设备池"""
        for device_name, device in temp_devices.items():
            self.devices[device_name] = device
            if hasattr(device, 'process_codes') and device.process_codes:
                for process_code in device.process_codes:
                    if process_code not in self.device_pools:
                        self.device_pools[process_code] = []
                    self.device_pools[process_code].append(device)
        self.logger.info(f"已添加 {len(temp_devices)} 个临时设备到设备池")

    def set_adjustments(self, adjustments: List[Dict], daily_work_time: float,
                        original_counts: Dict[str, int]) -> None:
        """存储设备调整配置"""
        self.adjustments_by_device = {}
        for adj in adjustments:
            device_name = adj.get('device_name')
            if not device_name:
                continue
            if device_name not in self.adjustments_by_device:
                self.adjustments_by_device[device_name] = []
            self.adjustments_by_device[device_name].append(adj)
        self.daily_work_time = daily_work_time
        self.original_device_counts = original_counts
        self.logger.info(f"DevicePool 已加载 {len(adjustments)} 个设备调整配置")

    def set_disable_schedule(self, schedule: Dict[str, List[Dict]]) -> None:
        """合并设备禁用时间表"""
        for device_id, windows in schedule.items():
            if device_id not in self.device_disable_schedule:
                self.device_disable_schedule[device_id] = []
            self.device_disable_schedule[device_id].extend(windows)

    def select_best_device(self, process_name: str) -> Optional[BaseDevice]:
        """选择最佳设备（空闲优先，否则负载最轻）"""
        all_devices = self.device_pools.get(process_name)
        if not all_devices:
            self.logger.error(f"时间 {self.env.now:.1f}: 找不到工序 {process_name} 的设备")
            return None

        now = self.env.now
        best_idle = None
        best_busy = None

        for d in all_devices:
            if not self.is_device_active(d.name, now):
                continue
            res = d.resource
            if len(res.queue) == 0 and res.count < res.capacity:
                # 空闲设备：选 historical_count 最小的
                if best_idle is None or d.historical_count < best_idle.historical_count:
                    best_idle = d
            else:
                # 忙碌设备：选负载最轻的
                key = (d.active_count, len(res.queue), d.historical_count)
                if best_busy is None or key < (best_busy.active_count, len(best_busy.resource.queue), best_busy.historical_count):
                    best_busy = d

        if best_idle is not None:
            return best_idle
        if best_busy is not None:
            return best_busy

        # 全部不活跃时回退到全量
        self.logger.warning(f"时间 {now:.1f}: 工序 {process_name} 没有活跃设备，使用所有设备")
        return min(all_devices, key=lambda d: (d.active_count, len(d.resource.queue), d.historical_count))

    def is_device_active(self, device_name: str, current_time: float) -> bool:
        """检查设备在指定时间是否活跃"""
        device = self.devices.get(device_name)
        if not device:
            return False

        if hasattr(device, 'is_temp') and device.is_temp:
            start_time = getattr(device, 'active_start_time', 0)
            end_time = getattr(device, 'active_end_time', float('inf'))
            return start_time <= current_time < end_time

        if device_name in self.device_disable_schedule:
            for schedule in self.device_disable_schedule[device_name]:
                if schedule['start_time'] <= current_time < schedule['end_time']:
                    return False

        return True

    def set_queue_sampling_enabled(self, enabled: bool) -> None:
        self.queue_sampling_enabled = enabled

    def sample_device_queue(self, device: BaseDevice) -> None:
        """采样设备队列长度（如果已启用）"""
        if self.queue_sampling_enabled:
            device.sample_queue_length()
