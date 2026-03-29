# -*- coding: utf-8 -*-
import simpy
from collections import deque
from typing import List, Dict, Any, Optional
from core.models import DeviceLog
from core.results import calculate_utilization

MAX_DEVICE_LOGS = 100_000
MAX_QUEUE_SAMPLES = 50_000


class BaseDevice:
    """基础设备类"""

    def __init__(self, env: simpy.Environment, name: str, capacity: int = 1):
        self.env = env
        self.name = name
        self.capacity = capacity
        self.resource = simpy.PriorityResource(env, capacity=capacity)

        self.total_busy_time = 0
        self.active_count = 0
        self.historical_count = 0
        self.total_processed_count = 0

        self.first_product_start_time = None
        self.last_product_end_time = None

        self.logs: deque = deque(maxlen=MAX_DEVICE_LOGS)
        self.process_codes = []
        self.queue_samples: deque = deque(maxlen=MAX_QUEUE_SAMPLES)

    def log_event(
        self,
        event: str,
        segment_id: str,
        *,
        event_type: Optional[str] = None,
        process_code: Optional[str] = None,
        product_id: Optional[str] = None,
        **kwargs,
    ):
        log_entry = DeviceLog(
            timestamp=self.env.now,
            event=event,
            segment_id=segment_id,
            start_time=kwargs.get('start_time', self.env.now),
            end_time=kwargs.get('end_time', self.env.now),
            duration=kwargs.get('duration', 0.0),
            event_type=event_type,
            process_code=process_code,
            product_id=product_id,
        )
        self.logs.append(log_entry)

    def sample_queue_length(self):
        """记录当前队列长度用于瓶颈分析"""
        sample = (self.env.now, len(self.resource.queue))
        if self.queue_samples and self.queue_samples[-1] == sample:
            return
        self.queue_samples.append(sample)

    def mark_process_start(self, start_time: float):
        self.active_count += 1
        self.historical_count += 1
        if self.first_product_start_time is None:
            self.first_product_start_time = start_time

    def mark_process_end(self, duration: float, processed_count: int = 1, end_time: float = None):
        end_time = self.env.now if end_time is None else end_time
        self.total_busy_time += duration
        self.total_processed_count += processed_count
        self.last_product_end_time = end_time

    def append_process_logs(
        self,
        event: str,
        segment_ids: List[str],
        start_time: float,
        end_time: float,
        duration: float,
        process_code: Optional[str] = None,
        event_type: Optional[str] = None,
        product_id: Optional[str] = None,
    ):
        for seg_id in segment_ids:
            self.log_event(
                event=event,
                segment_id=seg_id,
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                process_code=process_code,
                event_type=event_type,
                product_id=product_id,
            )

    def release_request(self, request):
        self.resource.release(request)
        if self.active_count > 0:
            self.active_count -= 1

    def get_utilization(self) -> float:
        if self.total_processed_count == 0 or self.first_product_start_time is None or self.last_product_end_time is None:
            return 0.0
        effective_time = self.last_product_end_time - self.first_product_start_time
        return calculate_utilization(self.total_busy_time, effective_time, as_percent=False)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.__class__.__name__,
            "processed_count": self.total_processed_count,
            "utilization": self.get_utilization(),
            "total_busy_time": self.total_busy_time,
            "average_process_time": (
                self.total_busy_time / self.total_processed_count
                if self.total_processed_count > 0 else 0
            )
        }
