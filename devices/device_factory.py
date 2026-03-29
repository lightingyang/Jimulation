# -*- coding: utf-8 -*-
"""设备工厂模块

负责从配置创建设备实例和临时设备。
从 SimulationEngine 中提取的设备创建逻辑。
"""

import logging
import simpy
from typing import Dict, Any, List, Tuple

from .base_device import BaseDevice


class DeviceFactory:
    """设备工厂"""

    def __init__(self, env: simpy.Environment):
        self.env = env
        self.logger = logging.getLogger('DeviceFactory')

    def create_devices_from_config(
        self, equipment_config: Dict[str, Any]
    ) -> Tuple[Dict[str, BaseDevice], Dict[str, int]]:
        """从设备配置创建所有设备实例

        Returns:
            devices: {device_id: BaseDevice}
            original_device_counts: {device_type: count}
        """
        devices = {}
        original_device_counts = {}

        for line_config in equipment_config.values():
            for device_type, device_info in line_config.get('device_groups', {}).items():
                count = device_info.get('count', 1)
                capacity = device_info.get('capacity', 1)

                original_device_counts[device_type] = count

                for i in range(count):
                    device_id = f"{device_type}{i+1:02d}"
                    device = BaseDevice(self.env, device_id, capacity=capacity)
                    device.process_codes = [device_type]
                    devices[device_id] = device

        return devices, original_device_counts

    def create_temp_devices(
        self,
        adjustments: List[Dict[str, Any]],
        original_device_counts: Dict[str, int],
    ) -> Tuple[Dict[str, BaseDevice], Dict[str, List[Dict]]]:
        """从设备调整配置创建临时设备和禁用时间表

        Returns:
            temp_devices: {temp_device_id: BaseDevice}
            disable_schedule: {device_id: [{start_time, end_time}]}
        """
        temp_devices = {}
        disable_schedule = {}

        for adj in adjustments:
            if adj.get('count') is None:
                continue

            device_type = adj['device_name']
            target_count = adj['count']
            original_count = original_device_counts.get(device_type, 0)

            if target_count > original_count:
                extra_count = target_count - original_count
                for i in range(extra_count):
                    temp_device_id = f"{device_type}_temp{i+1:02d}"

                    temp_device = BaseDevice(
                        env=self.env,
                        name=temp_device_id,
                        capacity=1
                    )
                    temp_device.process_codes = [device_type]
                    temp_device.is_temp = True
                    temp_device.active_start_time = adj['start_time']
                    temp_device.active_end_time = adj['end_time']

                    temp_devices[temp_device_id] = temp_device

                self.logger.info(f"为 {device_type} 创建了 {extra_count} 个临时设备")

            elif target_count < original_count:
                reduce_count = original_count - target_count
                for i in range(reduce_count):
                    device_idx = original_count - i
                    device_id = f"{device_type}{device_idx:02d}"

                    if device_id not in disable_schedule:
                        disable_schedule[device_id] = []
                    disable_schedule[device_id].append({
                        'start_time': adj['start_time'],
                        'end_time': adj['end_time']
                    })

                self.logger.info(f"标记 {device_type} 的 {reduce_count} 个设备在时间段内禁用")

        return temp_devices, disable_schedule
