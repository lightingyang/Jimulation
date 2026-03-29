# -*- coding: utf-8 -*-
"""FastAPI接口模块 -- 动态路由发现

子目录（如 chizy/, dficnb/）中的 routes.py 会被自动发现并注册。
删除某个子目录即可裁剪对应公司的功能。详见 GUIDE.md。
"""

import importlib
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

__all__ = ["create_api_app"]

APP_VERSION = "1.0.9.4"
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"
API_DOCS_URL = f"{API_PREFIX}/docs"
API_REDOC_URL = f"{API_PREFIX}/redoc"
CORS_OPTIONS = {
    "allow_origins": ["*"],
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}

logger = logging.getLogger("api")


def _configure_logging():
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _build_root_payload():
    return {
        "message": "Factory Simulation API",
        "version": APP_VERSION,
        "api_docs": API_DOCS_URL,
    }


def _discover_routers():
    """扫描 api/ 下的子目录，自动发现并加载各域的 router。"""
    package_dir = Path(__file__).resolve().parent
    for item in sorted(package_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("__"):
            continue
        if not (item / "routes.py").exists():
            continue
        module_name = f"api.{item.name}.routes"
        try:
            module = importlib.import_module(module_name)
            router = getattr(module, "router", None)
            if router is not None:
                yield router
                logger.info(f"已加载域路由: {item.name}")
        except Exception as e:
            logger.warning(f"加载域路由 {item.name} 失败: {e}")


def create_api_app():
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware

    from core.config_loader import load_config_data

    _configure_logging()

    app = FastAPI(
        title="Factory Simulation API",
        description="工厂仿真系统REST API服务",
        version=APP_VERSION,
        docs_url=API_DOCS_URL,
        redoc_url=API_REDOC_URL,
    )

    app.add_middleware(CORSMiddleware, **CORS_OPTIONS)

    # ── 内置系统端点（始终可用） ──────────────────────────────

    @app.get("/")
    async def root():
        return _build_root_payload()

    @app.get(f"{API_PREFIX}/health", tags=["系统"])
    async def health_check():
        """服务健康检查"""
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}

    @app.get(f"{API_PREFIX}/config_info", tags=["系统"])
    async def get_config_info(config_name: str = Query(..., description="配置文件名")):
        """获取配置文件信息"""
        try:
            config_type = config_name.replace("Config_", "") if config_name.startswith("Config_") else config_name
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "config", f"Config_{config_type}.yaml")

            if not os.path.exists(config_path):
                raise HTTPException(status_code=404, detail=f"配置文件不存在: Config_{config_type}.yaml")

            config_data = load_config_data(config_path)

            devices = [{"name": device_type, "count": device_config.get('count', 1)}
                      for line_info in config_data.get('equipment', {}).values()
                      for device_type, device_config in line_info.get('device_groups', {}).items()]

            return {"config_name": f"Config_{config_type}", "devices": devices}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取配置信息时发生错误: {str(e)}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"获取配置信息时发生错误: {str(e)}")

    # ── 动态注册各域路由 ──────────────────────────────────────

    for router in _discover_routers():
        app.include_router(router, prefix=API_PREFIX)

    return app
