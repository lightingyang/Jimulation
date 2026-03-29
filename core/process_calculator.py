# -*- coding: utf-8 -*-
"""工序计算模块 — 工序定义查询、方差计算"""

import random as _random_module
import logging
from typing import Dict, Optional

from .models import Process


class ProcessCalculator:
    """工序计算器：基础时长查询、方差"""

    def __init__(
        self,
        process_definitions: Dict[str, Process],
        process_code_mapping: Dict[str, str],
        rng: Optional[_random_module.Random] = None,
    ):
        self.process_definitions = process_definitions
        self.process_code_mapping = process_code_mapping
        self.rng = rng or _random_module.Random()
        self.logger = logging.getLogger('ProcessCalculator')

    def get_process_duration(self, process_name: str) -> float:
        """查询工序基础时长"""
        if process_name in self.process_definitions:
            return self.process_definitions[process_name].duration

        if process_name in self.process_code_mapping:
            mapped_name = self.process_code_mapping[process_name]
            if mapped_name in self.process_definitions:
                return self.process_definitions[mapped_name].duration

        raise ValueError(f"未找到工序 '{process_name}' 的定义")

    def calculate_duration(
        self,
        process_name: str,
        base_duration: float,
    ) -> float:
        """计算实际加工时长（含方差）"""
        actual = base_duration

        proc_def = self.process_definitions.get(process_name)
        if proc_def and proc_def.duration_variance > 0:
            v = proc_def.duration_variance
            actual *= self.rng.uniform(1 - v, 1 + v)

        return actual
