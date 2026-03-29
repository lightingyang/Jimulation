# -*- coding: utf-8 -*-
"""装配协调模块 — 基于 SimPy Event 的子件完成通知机制

父件（有 children 的 segment）等待所有子件完成后才开始处理自己的工序。
子件完成时调用 notify_child_complete() 触发事件。
"""

import simpy
import logging
from typing import Dict, List, Tuple


class AssemblyCoordinator:
    """装配协调器 — 子件完成事件驱动"""

    def __init__(self, env: simpy.Environment):
        self.env = env
        self.logger = logging.getLogger('AssemblyCoordinator')
        self.logger.setLevel(logging.WARNING)

        # parent → children 映射: key = (product_id, parent_segment_id)
        self.parent_children: Dict[Tuple[str, str], List[str]] = {}
        # child → parent 映射: key = (product_id, child_segment_id)
        self.child_parent: Dict[Tuple[str, str], str] = {}
        # 子件完成事件: key = (product_id, child_segment_id)
        self.child_events: Dict[Tuple[str, str], simpy.Event] = {}

    def register_product_segments(
        self, product_id: str, segments: list,
        assembly_tree: Dict[str, List[str]] = None
    ) -> None:
        """注册产品的装配关系"""
        assembly_tree = assembly_tree or {}
        for parent_id, child_ids in assembly_tree.items():
            self.parent_children[(product_id, parent_id)] = child_ids
            for cid in child_ids:
                self.child_parent[(product_id, cid)] = parent_id
                self.child_events[(product_id, cid)] = self.env.event()

    def get_children(self, product_id: str, segment_id: str) -> List[str]:
        """获取指定 segment 的子件 ID 列表"""
        return self.parent_children.get((product_id, segment_id), [])

    def wait_for_children(self, product_id: str, segment_id: str):
        """SimPy 生成器：等待所有子件完成"""
        children = self.get_children(product_id, segment_id)
        if not children:
            return
        events = [self.child_events[(product_id, cid)] for cid in children]
        yield self.env.all_of(events)

    def notify_child_complete(self, product_id: str, segment_id: str) -> None:
        """子件完成时调用，触发对应事件"""
        ev = self.child_events.get((product_id, segment_id))
        if ev and not ev.triggered:
            ev.succeed()
