# -*- coding: utf-8 -*-
"""跨域共用工具函数。

仅包含被多个域模块（chizy/, dficnb/）同时使用的函数。
单域独用的函数应放在对应域的 routes.py 中。
"""

from datetime import datetime
from functools import wraps
from typing import List, Dict, Any, Optional
import json
import logging
import os
import traceback
logger = logging.getLogger("api")

from core.simulation import SimulationEngine
from core.validators import validate_simulation_input


def flatten_product_config(product_config):
    """将嵌套 children 格式展平为 flat_segments + assembly_tree。

    处理规则：
    1. 递归遍历 segments 及其 children
    2. 有 children 的节点 → assembly_tree[parent_id] = [child_ids]
    3. 同名子件（segment_id == 父件 segment_id）→ 自动重命名为 {id}_pre
    4. 产品有 process_codes → 自动创建父 segment（ID=product_id）

    返回 (flat_segments: List[dict], assembly_tree: Dict[str, List[str]])
    """
    flat_segments = []
    assembly_tree = {}
    seen_ids = set()

    def _unique_id(desired_id):
        """确保 ID 唯一，同名时加 _pre 后缀"""
        if desired_id not in seen_ids:
            return desired_id
        candidate = f"{desired_id}_pre"
        counter = 2
        while candidate in seen_ids:
            candidate = f"{desired_id}_pre{counter}"
            counter += 1
        return candidate

    def _flatten(segment, parent_id=None):
        seg_id = segment.segment_id
        # 同名子件：如果 segment_id 与父节点相同，重命名
        if parent_id is not None and seg_id == parent_id:
            seg_id = _unique_id(f"{seg_id}_pre")
        else:
            seg_id = _unique_id(seg_id)
        seen_ids.add(seg_id)

        if segment.children:
            child_ids = []
            for child in segment.children:
                child_actual_id = _flatten(child, parent_id=seg_id)
                child_ids.append(child_actual_id)
            assembly_tree[seg_id] = child_ids

        flat_segments.append({
            'segment_id': seg_id,
            'process_codes': segment.process_codes,
        })
        return seg_id

    top_level_ids = []
    for seg in product_config.segments:
        actual_id = _flatten(seg)
        top_level_ids.append(actual_id)

    # 产品级 process_codes → 创建父 segment
    if product_config.process_codes:
        parent_id = _unique_id(product_config.product_id)
        seen_ids.add(parent_id)
        flat_segments.append({
            'segment_id': parent_id,
            'process_codes': product_config.process_codes,
        })
        assembly_tree[parent_id] = top_level_ids

    return flat_segments, assembly_tree
def simulation_error_handler(context_msg: str):
    """统一仿真端点异常处理装饰器"""
    from fastapi import HTTPException

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                raise
            except ValueError as e:
                logger.error(f"工序配置错误: {str(e)}")
                raise HTTPException(status_code=400, detail=f"工序配置错误: {str(e)}")
            except Exception as e:
                logger.error(f"{context_msg}: {str(e)}\n{traceback.format_exc()}")
                raise HTTPException(status_code=500, detail=f"{context_msg}: {str(e)}")
        return wrapper
    return decorator


def save_response_log(prefix: str, payload: Any):
    log_dir = os.getenv("LOG_DIR", "log")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filepath = os.path.join(log_dir, f"{prefix}_{timestamp}.json")

    if hasattr(payload, "model_dump"):
        log_data = payload.model_dump()
    else:
        log_data = dict(payload)
    log_data["timestamp"] = datetime.now().isoformat()
    try:
        with open(log_filepath, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, separators=(',', ':'))
    except Exception as e:
        logger.error(f"保存日志文件失败: {str(e)}")


def build_device_name(device_type: str, index: int, count: int) -> str:
    return f"{device_type}{index:02d}" if count > 1 else f"{device_type}01"


def iter_configured_devices(equipment_config: Dict[str, Any]):
    for group_name, group_info in equipment_config.items():
        for device_type, device_config in group_info.get('device_groups', {}).items():
            count = device_config.get('count', 1)
            for i in range(1, count + 1):
                yield group_name, device_type, build_device_name(device_type, i, count)


def add_products_to_simulator(
    simulator: SimulationEngine,
    source_configs: List[Any],
    id_attr: str,
    type_attr: str,
    work_order_map: Optional[Dict[str, str]] = None
):
    """将 Pydantic 请求模型转换为仿真引擎的 PipeProduct。

    这是 api/models.py (Pydantic) → core/models.py (Dataclass) 的桥梁。
    source_configs 中的每个元素是 ProductConfig (Pydantic),
    经过 simulator.build_product_from_dict() 转换为 PipeProduct (Dataclass)。
    """
    # 单次遍历：构建校验数据 + 收集每个源的预处理信息
    products_data = []
    prepared = []  # (idx, source_id, source_type, segments_data, assembly_tree)

    for idx, source in enumerate(source_configs):
        if not source.segments:
            continue

        source_id = getattr(source, id_attr)
        source_type = getattr(source, type_attr, 'standard')

        # 嵌套 children 展平为 flat_segments + assembly_tree
        segments_data, assembly_tree = flatten_product_config(source)

        products_data.append({
            'product_id': source_id,
            'segments': segments_data,
            'assembly_tree': assembly_tree,
        })
        prepared.append((idx, source_id, source_type, segments_data, assembly_tree, source))

    # 输入校验
    if products_data:
        from core.config_loader import load_config_data
        config_data = load_config_data(simulator.config_path)
        validation = validate_simulation_input(products_data, config_data)
        if validation.warnings:
            for w in validation.warnings:
                logger.warning(f"输入校验警告: {w}")
        if not validation.is_valid:
            raise ValueError(f"输入校验失败: {'; '.join(validation.errors)}")

    # 添加产品到仿真器
    for idx, source_id, source_type, segments_data, assembly_tree, source in prepared:
        if work_order_map is not None:
            work_order = getattr(source, 'work_order', '')
            for seg in segments_data:
                work_order_map[seg['segment_id']] = work_order

        product = simulator.build_product_from_dict(
            pipe_data={
                'product_id': source_id,
                'pipe_type': source_type,
                'segments': segments_data,
                'assembly_tree': assembly_tree,
                'priority': idx + 1
            },
            default_product_id=source_id,
            default_priority=idx + 1
        )
        if product is not None:
            simulator.add_product(product)


def run_simulation_with_sources(
    simulator: SimulationEngine,
    source_configs: List[Any],
    id_attr: str,
    type_attr: str,
    work_order_map: Optional[Dict[str, str]] = None,
    until: Optional[float] = None,
) -> None:
    add_products_to_simulator(
        simulator=simulator,
        source_configs=source_configs,
        id_attr=id_attr,
        type_attr=type_attr,
        work_order_map=work_order_map,
    )
    simulator.run(until=until or simulator.simulation_duration)
