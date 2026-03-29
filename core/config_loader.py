# -*- coding: utf-8 -*-
"""Shared helpers for loading static YAML configuration."""

import logging
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_UNCOILING_VENDOR_CONFIG_PATH = BASE_DIR / "config" / "Config_DFICNB.yaml"

_config_cache: Dict[str, Dict[str, Any]] = {}


def load_config_data(config_path: str) -> Dict[str, Any]:
    if config_path in _config_cache:
        return _config_cache[config_path]
    try:
        with Path(config_path).open("r", encoding="utf-8-sig") as file_obj:
            data = yaml.safe_load(file_obj) or {}
    except yaml.YAMLError as e:
        logger.error(f"YAML 配置解析失败 {config_path}: {e}")
        raise
    _config_cache[config_path] = data
    return data


def load_equipment_config(config_path: str) -> Dict[str, Any]:
    return load_config_data(config_path).get("equipment", {})


def load_uncoiling_vendor_config(config_path: str | None = None) -> Dict[str, Any]:
    target_path = str(Path(config_path)) if config_path else str(DEFAULT_UNCOILING_VENDOR_CONFIG_PATH)
    preprocessing = load_config_data(target_path).get("preprocessing", {})
    return preprocessing.get("manufacturer_efficiency", {})


def load_preprocessing_config(config_path: str | None = None) -> Dict[str, Any]:
    target_path = str(Path(config_path)) if config_path else str(DEFAULT_UNCOILING_VENDOR_CONFIG_PATH)
    return load_config_data(target_path).get("preprocessing", {})
