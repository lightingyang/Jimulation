# -*- coding: utf-8 -*-
"""输入校验模块

在仿真启动前校验产品数据、装配树、工序代码和设备调整的合法性。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class ValidationResult:
    """校验结果"""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def validate_simulation_input(
    products_data: List[Dict[str, Any]],
    config_data: Dict[str, Any],
    adjustments: Optional[List[Dict[str, Any]]] = None,
) -> ValidationResult:
    """统一校验入口 — 单遍扫描产品数据，合并产品/装配树/工序代码校验"""
    errors = []
    warnings = []
    errors_append = errors.append
    warnings_append = warnings.append

    process_defs = config_data.get('process_definitions', {})
    equipment_config = config_data.get('equipment', {})

    # 预构建设备类型集合 + 原始设备数量（单遍扫描）
    device_types: set = set()
    original_counts: Dict[str, int] = {}
    if equipment_config:
        for line_cfg in equipment_config.values():
            dg = line_cfg.get('device_groups')
            if dg:
                device_types.update(dg)
                for dt, di in dg.items():
                    original_counts[dt] = di.get('count', 1)

    check_devices = bool(device_types)
    unknown_codes: set = set()
    no_device_codes: set = set()
    seen_product_ids: set = set()

    # ── 单遍扫描：产品 + 装配树 + 工序代码 ──
    for product in products_data:
        pid = product.get('product_id', '')
        segments = product.get('segments', [])
        tree = product.get('assembly_tree', {})

        # 产品ID唯一
        if pid in seen_product_ids:
            errors_append(f"产品ID重复: '{pid}'")
        else:
            seen_product_ids.add(pid)

        # 至少有一个segment
        if not segments:
            errors_append(f"产品 '{pid}' 没有segment")
            continue

        # segment遍历：去重 + 工序码收集
        seg_ids: set = set()
        assembly_parents = set(tree) if tree else None

        for seg in segments:
            sid = seg.get('segment_id', '')
            if sid in seg_ids:
                errors_append(f"产品 '{pid}' 中segment_id重复: '{sid}'")
            else:
                seg_ids.add(sid)

            codes = seg.get('process_codes', [])
            if not codes:
                if assembly_parents is None or sid not in assembly_parents:
                    warnings_append(f"产品 '{pid}' 的segment '{sid}' 没有工序代码")
            else:
                for code in codes:
                    if code not in process_defs:
                        unknown_codes.add(code)
                    if check_devices and code not in device_types:
                        no_device_codes.add(code)

        # 装配树校验（内联，仅当有树时）
        if tree:
            for parent_id, children in tree.items():
                if parent_id not in seg_ids:
                    errors_append(
                        f"产品 '{pid}' 的装配树父节点 '{parent_id}' 不存在于segment列表中"
                    )
                for child_id in children:
                    if child_id not in seg_ids:
                        errors_append(
                            f"产品 '{pid}' 的装配树子节点 '{child_id}' (父节点 '{parent_id}') 不存在于segment列表中"
                        )

            # 环检测（仅当树有多个层级的父节点时才需要）
            if len(tree) > 1:
                _detect_cycle(tree, pid, errors_append)

    # 工序代码汇总报错
    if unknown_codes:
        for code in sorted(unknown_codes):
            errors_append(f"工序代码 '{code}' 在配置的process_definitions中未定义")

    # 设备组警告（排除已报错的未知代码）
    no_device_codes -= unknown_codes
    if no_device_codes:
        for code in sorted(no_device_codes):
            warnings_append(f"工序代码 '{code}' 没有对应的设备组")

    # 提前返回：产品数据不合法时跳过设备调整校验
    result = ValidationResult(errors=errors, warnings=warnings)
    if not result.is_valid or not adjustments:
        return result

    # 设备调整校验（original_counts 已在上方预构建）
    for i, adj in enumerate(adjustments):
        dn = adj.get('device_name', '')
        if dn and dn not in original_counts:
            errors_append(f"设备调整[{i}]: 设备类型 '{dn}' 不存在于配置中")

        st = adj.get('start_time')
        et = adj.get('end_time')
        if st is not None and et is not None:
            if st < 0:
                errors_append(f"设备调整[{i}]: start_time ({st}) 不能为负")
            if et < 0:
                errors_append(f"设备调整[{i}]: end_time ({et}) 不能为负")
            if st >= et:
                errors_append(f"设备调整[{i}]: start_time ({st}) 必须小于 end_time ({et})")

        cnt = adj.get('count')
        if cnt is not None and cnt < 0:
            errors_append(f"设备调整[{i}]: count ({cnt}) 不能为负")

    return result


def _detect_cycle(tree: Dict[str, list], product_id: str, errors_append) -> None:
    """迭代式DFS环检测（仅检查同时也是父节点的子节点）"""
    VISITING, VISITED = 1, 2
    state: Dict[str, int] = {}

    for root in tree:
        if root in state:
            continue
        stack = [(root, True)]
        while stack:
            node, entering = stack.pop()
            if entering:
                s = state.get(node)
                if s == VISITED:
                    continue
                if s == VISITING:
                    errors_append(f"产品 '{product_id}' 的装配树存在环，涉及节点 '{node}'")
                    break
                state[node] = VISITING
                stack.append((node, False))
                children = tree.get(node)
                if children:
                    for child in children:
                        if child in tree:
                            cs = state.get(child)
                            if cs == VISITING:
                                errors_append(f"产品 '{product_id}' 的装配树存在环，涉及节点 '{child}'")
                            elif cs is None:
                                stack.append((child, True))
            else:
                state[node] = VISITED


def validate_engine_ready(products: Dict, device_pool) -> ValidationResult:
    """仿真启动前兜底校验"""
    result = ValidationResult()
    if not products:
        result.errors.append("没有添加任何产品，无法启动仿真")
    return result
