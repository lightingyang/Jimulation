# -*- coding: utf-8 -*-
"""工厂仿真数据模型"""

from enum import Enum
from typing import List, Dict, Optional, Union
from dataclasses import dataclass, field


class Status(str, Enum):
    """Segment/Product 生命周期状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


_VALID_TRANSITIONS: Dict[Status, frozenset] = {
    Status.PENDING:     frozenset({Status.IN_PROGRESS, Status.FAILED}),
    Status.IN_PROGRESS: frozenset({Status.COMPLETED, Status.FAILED}),
    Status.COMPLETED:   frozenset(),
    Status.FAILED:      frozenset(),
}


class _StatusMixin:
    """为 Segment 和 PipeProduct 提供状态转换验证的 Mixin"""

    _status: Status

    def __post_init__(self):
        if isinstance(self._status, str):
            self._status = Status(self._status)

    @property
    def status(self) -> str:
        return self._status.value

    @status.setter
    def status(self, value: str) -> None:
        self.transition_to(value)

    def transition_to(self, new_status) -> None:
        target = Status(new_status) if isinstance(new_status, str) else new_status
        current = self._status
        if target not in _VALID_TRANSITIONS[current]:
            raise ValueError(
                f"非法状态转换: {current.value} -> {target.value} "
                f"(允许: {[s.value for s in _VALID_TRANSITIONS[current]]})"
            )
        self._status = target


@dataclass
class DeviceLog:
    """设备日志条目"""
    timestamp: float
    event: str
    segment_id: str
    start_time: float
    end_time: float
    duration: float
    event_type: Optional[str] = None
    process_code: Optional[str] = None
    product_id: Optional[str] = None


@dataclass
class Process:
    """工序定义"""
    name: str
    duration: float
    setup_time: float = 0.0
    cooling_time: float = 0.0
    duration_variance: float = 0.0


@dataclass
class Segment(_StatusMixin):
    """管段类"""
    segment_id: str
    product_id: str
    process_codes: Union[str, List[str]]
    pipe_type: str
    priority: int = 1
    segment_type: str = "standard"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    _status: Status = field(default=Status.PENDING, repr=False)

    def __post_init__(self):
        _StatusMixin.__post_init__(self)


@dataclass
class PipeProduct(_StatusMixin):
    """管道产品类"""
    product_id: str
    pipe_type: str
    segments: List[Segment]
    product_type: str = "standard"
    priority: int = 1
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    _status: Status = field(default=Status.PENDING, repr=False)
    assembly_tree: Dict[str, List[str]] = field(default_factory=dict)

    def __post_init__(self):
        _StatusMixin.__post_init__(self)
