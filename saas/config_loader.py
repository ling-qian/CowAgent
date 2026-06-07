# encoding:utf-8
"""
租户级配置加载器

当 saas_mode=True 时，conf_tenant() 会在全局配置基础上叠加租户级配置覆盖。
租户配置存储在 tenants.config_json 字段中（JSON 格式）。
"""

import json
from typing import Optional

from common.log import logger
from common.tenant import current_tenant_id
from config import conf, Config


# 租户配置缓存（tenant_id -> Config 实例）
_tenant_config_cache: dict = {}


def conf_tenant(tenant_id: str = None) -> Config:
    """获取当前租户的配置。

    优先级：租户级覆盖 > 全局配置 > available_setting 默认值

    Args:
        tenant_id: 显式指定租户ID，不传则从 TenantContext 获取

    Returns:
        Config 实例（全局配置 + 租户覆盖）
    """
    from config import config as global_config

    # saas_mode 关闭时直接返回全局配置
    if not global_config.get("saas_mode", False):
        return conf()

    tid = tenant_id or current_tenant_id()
    if tid is None:
        return conf()

    # 检查缓存
    if tid in _tenant_config_cache:
        return _tenant_config_cache[tid]

    # 加载租户级配置覆盖
    tenant_overrides = _load_tenant_overrides(tid)

    # 合并：以全局配置为基础，用租户覆盖合并
    merged = Config(dict(global_config))
    if tenant_overrides:
        for k, v in tenant_overrides.items():
            merged[k] = v

    # 缓存
    _tenant_config_cache[tid] = merged
    return merged


def _load_tenant_overrides(tenant_id: str) -> Optional[dict]:
    """从数据库加载租户级配置覆盖，数据库不可用时返回 None

    查找顺序：
    1. Redis/内存路由缓存
    2. 数据库
    """
    # 1. 检查路由缓存
    try:
        from saas.cache import lookup_tenant_config, cache_tenant_config
        cached = lookup_tenant_config(tenant_id)
        if cached is not None:
            return cached
    except Exception:
        pass

    # 2. 查询数据库
    try:
        from saas.database import Tenant
        tenant = Tenant.query.filter_by(id=tenant_id, is_active=True).first()
        if tenant and tenant.config_json:
            overrides = json.loads(tenant.config_json)
            # 写入路由缓存
            try:
                cache_tenant_config(tenant_id, overrides)
            except Exception:
                pass
            return overrides
    except ImportError:
        # flask_sqlalchemy 未安装，降级为全局配置
        logger.debug(f"[config_loader] SaaS dependencies not installed, using global config")
    except Exception as e:
        logger.warning(f"[config_loader] Failed to load tenant config for {tenant_id}: {e}")
    return None


def invalidate_tenant_config(tenant_id: str = None):
    """清除租户配置缓存，下次 conf_tenant() 调用时重新加载"""
    if tenant_id:
        _tenant_config_cache.pop(tenant_id, None)
        # 同时清除路由缓存
        try:
            from saas.cache import invalidate_tenant_config as invalidate_route_cache
            invalidate_route_cache(tenant_id)
        except Exception:
            pass
    else:
        _tenant_config_cache.clear()


def get_tenant_config(key: str, default=None, tenant_id: str = None):
    """快捷函数：获取当前租户的某个配置项"""
    return conf_tenant(tenant_id).get(key, default)
