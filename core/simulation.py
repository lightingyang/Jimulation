# -*- coding: utf-8 -*-
"""仿真引擎模块

SimulationEngine 是整个仿真系统的入口和编排者：
1. 从 YAML 加载工序配置 → 构建 Process 对象
2. 接收 PipeProduct 列表 → 为每个 Segment 创建 SimPy 进程
3. 通过 ProcessFlowManager 编排工序执行：
   - 设备竞争通过 SimPy PriorityResource 实现
   - 装配约束通过 AssemblyCoordinator 的 Event 等待实现
4. 仿真完成后由 ResultsManager 收集设备利用率等指标

时间单位: 分钟。daily_work_time=480 表示 8 小时/天。
"""

import os
import random as _random_module
import simpy
import logging
from typing import List, Dict, Any, Optional

from .config_loader import load_config_data
from .models import PipeProduct, Segment, Process
from .process_flow import ProcessFlowManager
from .process_calculator import ProcessCalculator
from .assembly_coordinator import AssemblyCoordinator
from .results import ResultsManager
from .work_schedule import WorkSchedule
from devices import DeviceFactory, DevicePool


class SimulationEngine:
    """仿真引擎"""

    def __init__(self, simulation_duration: Optional[float] = None, config_type: Optional[str] = None,
                 daily_work_time: float = 480.0, random_seed: Optional[int] = None):
        if not simulation_duration or simulation_duration <= 0:
            raise ValueError("simulation_duration 必须大于0")
        if not config_type:
            raise ValueError("config_type 必须明确指定")

        self.env = simpy.Environment()
        self.simulation_duration = simulation_duration  # 24h 制，含休息时间
        self.config_type = config_type
        self.daily_work_time = daily_work_time
        self.rng = _random_module.Random(random_seed)
        self.products = {}
        self.logger = self._setup_logger()

        self.devices = {}
        self.process_definitions = {}
        self.process_code_mapping = {}
        self.device_adjustments = []
        self.original_device_counts = {}
        self.temp_devices = {}
        self.device_factory = DeviceFactory(self.env)
        self.device_pool = DevicePool(self.env)
        self.assembly_coordinator = AssemblyCoordinator(self.env)

        # 工作日程（24 小时制：工作 + 休息）
        self.work_schedule = WorkSchedule(daily_work_time=daily_work_time)

        self.config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                        'config', f"Config_{config_type}.yaml")
        self._load_default_configuration()

        # 配置加载完毕后构建 ProcessCalculator 和 ProcessFlowManager
        self.process_calculator = ProcessCalculator(
            process_definitions=self.process_definitions,
            process_code_mapping=self.process_code_mapping,
            rng=self.rng,
        )
        self.process_flow_manager = ProcessFlowManager(
            env=self.env,
            device_pool=self.device_pool,
            process_calculator=self.process_calculator,
            assembly_coordinator=self.assembly_coordinator,
            work_schedule=self.work_schedule,
        )

    def _setup_logger(self):
        logger = logging.getLogger('SimulationEngine')
        return logger

    def _load_default_configuration(self):
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")

        config_data = load_config_data(self.config_path)

        if 'process_definitions' in config_data:
            process_objects = {}
            for process_code, process_data in config_data['process_definitions'].items():
                if 'duration' not in process_data:
                    raise ValueError(f"工序 '{process_code}' 缺少 'duration' 配置")
                process_objects[process_code] = Process(
                    name=process_code,
                    duration=process_data['duration'],
                    setup_time=process_data.get('setup_time', 0.0),
                    cooling_time=process_data.get('cooling_time', 0.0),
                    duration_variance=process_data.get('duration_variance', 0.0),
                )
            self.process_definitions = process_objects

        if 'equipment' in config_data:
            self._load_equipment_config(config_data['equipment'])

        if 'process_code_mapping' in config_data:
            self.process_code_mapping = config_data['process_code_mapping']

    def _load_equipment_config(self, equipment_config: Dict[str, Any]):
        try:
            devices, original_device_counts = \
                self.device_factory.create_devices_from_config(equipment_config)

            self.devices = devices
            self.original_device_counts = original_device_counts
            self.device_pool.initialize(devices)
        except Exception as e:
            self.logger.exception(f"设备配置加载失败: {e}")

    def set_device_adjustments(self, adjustments: List[Dict[str, Any]]):
        self.device_adjustments = adjustments

        self.device_pool.set_adjustments(
            adjustments,
            self.daily_work_time,
            self.original_device_counts
        )

        # 将 adjusted_time 转换为 WorkSchedule 加班配置
        for adj in adjustments:
            adjusted_time = adj.get('adjusted_time')
            if adjusted_time is None or adjusted_time <= self.daily_work_time:
                continue
            device_type = adj.get('device_name')
            extra_minutes = adjusted_time - self.daily_work_time
            # 根据 start_time 推算是第几天（基于旧的连续时间线）
            start_time = adj.get('start_time', 0)
            day = int(start_time / self.daily_work_time) + 1
            self.work_schedule.set_overtime(device_type, day, extra_minutes)

        self._prepare_device_count_adjustments()

    def set_queue_sampling_enabled(self, enabled: bool):
        self.device_pool.set_queue_sampling_enabled(enabled)

    def _prepare_device_count_adjustments(self):
        temp_devices, disable_schedule = self.device_factory.create_temp_devices(
            self.device_adjustments, self.original_device_counts
        )
        self.temp_devices = temp_devices
        self.device_pool.set_disable_schedule(disable_schedule)

    def build_segment_from_dict(
        self,
        segment_data: Dict[str, Any],
        product_id: str,
        pipe_type: str,
        priority: int = 1,
        default_segment_id: Optional[str] = None
    ) -> Segment:
        process_codes = segment_data.get('process_codes', [])

        return Segment(
            segment_id=segment_data.get('segment_id', default_segment_id or "seg_0"),
            product_id=product_id,
            segment_type=segment_data.get('segment_type', 'standard'),
            process_codes=process_codes,
            pipe_type=segment_data.get('pipe_type', pipe_type),
            priority=priority
        )

    def build_product_from_dict(
        self,
        pipe_data: Dict[str, Any],
        default_product_id: str,
        default_priority: int
    ) -> Optional[PipeProduct]:
        product_id = pipe_data.get('product_id', default_product_id)
        pipe_type = pipe_data.get('pipe_type', 'standard')
        assembly_tree = pipe_data.get('assembly_tree', {})
        segments = []

        for idx, segment_data in enumerate(pipe_data.get('segments', [])):
            segment = self.build_segment_from_dict(
                segment_data=segment_data,
                product_id=product_id,
                pipe_type=pipe_type,
                priority=default_priority,
                default_segment_id=f"seg_{idx}"
            )
            segments.append(segment)

        if not segments:
            return None

        return PipeProduct(
            product_id=product_id,
            pipe_type=pipe_type,
            segments=segments,
            product_type=pipe_data.get('product_type', 'standard'),
            priority=pipe_data.get('priority', default_priority),
            assembly_tree=assembly_tree
        )

    def add_product(self, product: PipeProduct):
        self.products[product.product_id] = product

    def run(self, until: float = None) -> None:
        from .validators import validate_engine_ready
        validation = validate_engine_ready(self.products, self.device_pool)
        if not validation.is_valid:
            raise ValueError(f"仿真启动校验失败: {'; '.join(validation.errors)}")

        if until is not None:
            self.simulation_duration = until

        if self.temp_devices:
            self.device_pool.add_temp_devices(self.temp_devices)

        for product in self.products.values():
            self.env.process(self.process_product(product))

        self.env.run(until=self.simulation_duration)

    def get_results(
        self,
        include_device_statistics: bool = True,
        include_work_logs: bool = True,
        include_product_results: bool = True,
    ) -> Dict[str, Any]:
        return ResultsManager.get_simulation_results(
            products=list(self.products.values()),
            devices=self.device_pool.devices,
            simulation_time=self.work_schedule.sim_time_to_work_minutes(self.simulation_duration),
            include_device_statistics=include_device_statistics,
            include_work_logs=include_work_logs,
            include_product_results=include_product_results,
        )

    def process_product(self, product: PipeProduct):
        try:
            product.transition_to('in_progress')

            self.assembly_coordinator.register_product_segments(
                product.product_id,
                product.segments,
                product.assembly_tree
            )

            # 分离叶子和父节点
            assembly_parents = set(product.assembly_tree.keys()) if product.assembly_tree else set()
            leaf_segments = [seg for seg in product.segments if seg.segment_id not in assembly_parents]
            parent_segments = [seg for seg in product.segments if seg.segment_id in assembly_parents]

            # 叶子节点带错开延迟启动
            leaf_processes = [
                self.env.process(self.process_segment_with_delay(seg, i * 0.1))
                for i, seg in enumerate(leaf_segments)
            ]

            # 父节点也启动（内部会等子件完成事件），无延迟
            parent_processes = [
                self.env.process(self.process_segment_with_delay(seg, 0))
                for seg in parent_segments
            ]

            yield self.env.all_of(leaf_processes + parent_processes)

            start_times = [s.start_time for s in product.segments if s.start_time is not None]
            product.start_time = min(start_times) if start_times else self.env.now
            product.end_time = self.env.now

            completed = sum(1 for s in product.segments if s.status == 'completed')
            failed = sum(1 for s in product.segments if s.status == 'failed')

            if completed == len(product.segments):
                product.transition_to('completed')
            elif failed:
                product.transition_to('failed')

        except Exception as e:
            self.logger.exception(f"产品 {product.product_id} 处理失败: {e}")
            product.transition_to('failed')
            product.end_time = self.env.now

    def process_segment_with_delay(self, segment: Segment, delay: float):
        try:
            if delay > 0:
                yield self.env.timeout(delay)
            yield self.env.process(self.process_segment(segment))
        except Exception as e:
            self.logger.exception(f"管段 {segment.segment_id} 延迟处理失败: {e}")
            if segment.status != 'failed':
                segment.transition_to('failed')
            segment.end_time = self.env.now

    def process_segment(self, segment: Segment):
        try:
            yield self.env.process(
                self.process_flow_manager.process_segment(segment)
            )
        except Exception as e:
            self.logger.exception(f"管段 {segment.segment_id} 处理失败: {e}")
            if segment.status != 'failed':
                segment.transition_to('failed')
            segment.end_time = self.env.now
