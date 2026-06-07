# encoding:utf-8
"""
IM 渠道租户解析工具

根据 IM 平台的 AppID 查找对应的 tenant_id，
在 IM 渠道收到回调时自动设置租户上下文。

用法（在 IM 渠道启动或收到回调时）:
    from saas.im_tenant import resolve_im_tenant
    tenant_id = resolve_im_tenant("feishu", "cli_xxxxx")
    if tenant_id:
        TenantContext.set_tenant(tenant_id)
"""

import threading
from typing import Optional

from common.log import logger
from common.tenant import TenantContext

# 内存缓存：{(channel_type, app_id): tenant_id}
_cache: dict[tuple[str, str], Optional[str]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 缓存 5 分钟
_cache_timestamp: float = 0


def resolve_im_tenant(channel_type: str, app_id: str) -> Optional[str]:
    """根据 IM 渠道类型和 AppID 解析 tenant_id

    查找顺序：
    1. 内存缓存
    2. Redis/内存路由缓存
    3. 数据库 im_channel_mappings 表
    4. 租户 config_json 中的 IM 配置（兼容旧数据）

    Args:
        channel_type: 渠道类型 (feishu/dingtalk/wechat_mp/wechat_com/wechat_kf/wecom_bot)
        app_id: IM 平台的应用 ID

    Returns:
        tenant_id 或 None
    """
    if not channel_type or not app_id:
        return None

    cache_key = (channel_type, app_id)

    # 1. 检查本地内存缓存
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    # 2. 检查 Redis/路由缓存
    try:
        from saas.cache import lookup_im_channel_mapping, cache_im_channel_mapping
        cached = lookup_im_channel_mapping(channel_type, app_id)
        if cached:
            with _cache_lock:
                _cache[cache_key] = cached
            return cached
    except Exception:
        pass

    # 3. 查询数据库
    tenant_id = _lookup_from_db(channel_type, app_id)
    if tenant_id:
        with _cache_lock:
            _cache[cache_key] = tenant_id
        # 写入路由缓存
        try:
            cache_im_channel_mapping(channel_type, app_id, tenant_id)
        except Exception:
            pass
        return tenant_id

    # 4. 兼容：从租户 config_json 中查找
    tenant_id = _lookup_from_config(channel_type, app_id)
    if tenant_id:
        with _cache_lock:
            _cache[cache_key] = tenant_id
        try:
            cache_im_channel_mapping(channel_type, app_id, tenant_id)
        except Exception:
            pass
        return tenant_id

    # 缓存 None 避免反复查询
    with _cache_lock:
        _cache[cache_key] = None
    return None


def _lookup_from_db(channel_type: str, app_id: str) -> Optional[str]:
    """从 im_channel_mappings 表查找"""
    try:
        from saas.database import IMChannelMapping
        mapping = IMChannelMapping.query.filter_by(
            channel_type=channel_type, app_id=app_id, is_active=True
        ).first()
        return mapping.tenant_id if mapping else None
    except Exception as e:
        logger.debug(f"[IM Tenant] DB lookup failed: {e}")
        return None


def _lookup_from_config(channel_type: str, app_id: str) -> Optional[str]:
    """从租户 config_json 中查找 IM 配置（兼容旧数据）

    配置键映射：
    - feishu → feishu_app_id
    - dingtalk → dingtalk_client_id
    - wechat_mp → wechat_mp_app_id
    - wechat_com → wechat_com_corp_id
    - wechat_kf → wechat_kf_corp_id
    - wecom_bot → wecom_bot_key
    """
    config_key_map = {
        "feishu": "feishu_app_id",
        "dingtalk": "dingtalk_client_id",
        "wechat_mp": "wechat_mp_app_id",
        "wechat_com": "wechat_com_corp_id",
        "wechat_kf": "wechat_kf_corp_id",
        "wecom_bot": "wecom_bot_key",
    }
    config_key = config_key_map.get(channel_type)
    if not config_key:
        return None

    try:
        from saas.database import Tenant
        import json

        tenants = Tenant.query.filter_by(is_active=True).all()
        for tenant in tenants:
            if not tenant.config_json:
                continue
            try:
                cfg = json.loads(tenant.config_json)
                if cfg.get(config_key) == app_id:
                    return tenant.id
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception as e:
        logger.debug(f"[IM Tenant] Config lookup failed: {e}")

    return None


def set_im_tenant_context(channel_type: str, app_id: str) -> Optional[str]:
    """解析 IM 渠道的 tenant_id 并设置租户上下文

    在 IM 渠道收到回调时调用此函数，自动设置 TenantContext。

    Args:
        channel_type: 渠道类型
        app_id: IM 平台的应用 ID

    Returns:
        解析到的 tenant_id 或 None
    """
    tenant_id = resolve_im_tenant(channel_type, app_id)
    if tenant_id:
        TenantContext.set_tenant(tenant_id)
        logger.debug(f"[IM Tenant] Set tenant context: {tenant_id} for {channel_type}:{app_id}")
    return tenant_id


def clear_cache():
    """清空缓存（用于测试或配置变更后刷新）"""
    with _cache_lock:
        _cache.clear()
