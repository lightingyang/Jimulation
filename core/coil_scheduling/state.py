# -*- coding: utf-8 -*-
"""Mutable scheduling state."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass(slots=True)
class ScheduleState:
    day_task_map: Dict[int, Dict[int, Any]] = field(default_factory=dict)
    day_segment_used: Dict[int, List[float]] = field(default_factory=dict)
    day_spec_sheet_counts: Dict[int, Dict[Tuple[Any, ...], int]] = field(default_factory=dict)
    day_used: Dict[int, float] = field(default_factory=dict)

    def get_day_used(self, day: int) -> float:
        return self.day_used.get(day, 0.0)

    def add_day_used(self, day: int, delta_minutes: float) -> None:
        updated = self.day_used.get(day, 0.0) + delta_minutes
        if updated > 1e-9:
            self.day_used[day] = updated
        else:
            self.day_used.pop(day, None)

    def rebuild_day_used(self) -> Dict[int, float]:
        self.day_used = {
            day: sum(segment_minutes)
            for day, segment_minutes in self.day_segment_used.items()
            if sum(segment_minutes) > 1e-9
        }
        return self.day_used
