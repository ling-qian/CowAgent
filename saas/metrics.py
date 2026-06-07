# encoding:utf-8
"""
Prometheus 监控指标

为 SaaS 平台提供标准 Prometheus 指标，包括：
- 请求总量/延迟/错误率
- API Key 认证成功/失败
- 租户活跃度
- 缓存命中率
- 配额使用率

依赖：prometheus_client（轻量级，纯 Python）

用法：
    from saas.metrics import metrics_app, REQUEST_COUNT, CACHE_HITS
    # 在 Flask app 中注册 /metrics 端点
    saas_app.wsgi_app = metrics_app(saas_app.wsgi_app)
    # 在代码中记录指标
    REQUEST_COUNT.labels(method='GET', endpoint='/api/keys', status=200).inc()
"""

import os
import time
import threading
from typing import Optional

# ---------------------------------------------------------------------------
# 指标定义（懒加载，prometheus_client 未安装时降级为空操作）
# ---------------------------------------------------------------------------

_metrics_enabled = False

try:
    from prometheus_client import (
        Counter, Histogram, Gauge, Info,
        generate_latest, CONTENT_TYPE_LATEST,
        CollectorRegistry, REGISTRY,
    )
    _metrics_enabled = True
except ImportError:
    pass


def _make_counter(name, desc, labels):
    if _metrics_enabled:
        return Counter(name, desc, labels)
    return _NullMetric()


def _make_histogram(name, desc, labels, buckets=None):
    if _metrics_enabled:
        kwargs = {"labelnames": labels}
        if buckets:
            kwargs["buckets"] = buckets
        return Histogram(name, desc, **kwargs)
    return _NullMetric()


def _make_gauge(name, desc, labels):
    if _metrics_enabled:
        return Gauge(name, desc, labels)
    return _NullMetric()


class _NullMetric:
    """prometheus_client 未安装时的空操作指标"""
    def labels(self, *args, **kwargs):
        return self
    def inc(self, *args, **kwargs):
        pass
    def dec(self, *args, **kwargs):
        pass
    def set(self, *args, **kwargs):
        pass
    def observe(self, *args, **kwargs):
        pass


# ---------------------------------------------------------------------------
# 指标实例
# ---------------------------------------------------------------------------

# 请求计数
REQUEST_COUNT = _make_counter(
    "cowagent_request_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status_code"],
)

# 请求延迟
REQUEST_LATENCY = _make_histogram(
    "cowagent_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# API Key 认证
AUTH_ATTEMPTS = _make_counter(
    "cowagent_auth_attempts_total",
    "Total API key authentication attempts",
    ["result"],  # success / invalid_key / tenant_inactive
)

# 活跃租户数
ACTIVE_TENANTS = _make_gauge(
    "cowagent_active_tenants",
    "Number of active tenants",
    [],
)

# 缓存命中/未命中
CACHE_HITS = _make_counter(
    "cowagent_cache_hits_total",
    "Cache hit count",
    ["cache_type"],  # api_key / tenant_config / im_channel
)

CACHE_MISSES = _make_counter(
    "cowagent_cache_misses_total",
    "Cache miss count",
    ["cache_type"],
)

# 配额使用
QUOTA_USAGE = _make_gauge(
    "cowagent_quota_usage_ratio",
    "Quota usage ratio (0-1)",
    ["tenant_id", "plan"],
)

# LLM 调用
LLM_CALL_COUNT = _make_counter(
    "cowagent_llm_calls_total",
    "Total LLM API calls",
    ["tenant_id", "model"],
)

LLM_CALL_LATENCY = _make_histogram(
    "cowagent_llm_call_duration_seconds",
    "LLM API call latency",
    ["model"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

LLM_TOKEN_USAGE = _make_counter(
    "cowagent_llm_tokens_total",
    "Total LLM token usage",
    ["tenant_id", "model", "type"],  # type: prompt / completion
)


# ---------------------------------------------------------------------------
# Flask 中间件
# ---------------------------------------------------------------------------

def metrics_middleware(app):
    """为 Flask 应用添加 Prometheus 请求指标中间件"""
    if not _metrics_enabled:
        return

    @app.before_request
    def _before():
        from flask import g
        g._prom_start_time = time.time()

    @app.after_request
    def _after(response):
        from flask import g, request
        start = getattr(g, "_prom_start_time", None)
        if start:
            duration = time.time() - start
            endpoint = request.path
            # 截断动态路径段（如 /api/keys/xxx → /api/keys/:id）
            parts = endpoint.split("/")
            normalized = []
            for p in parts:
                if len(p) > 20 or (p and not p.isalnum() and "-" not in p and "_" not in p):
                    normalized.append(":id")
                else:
                    normalized.append(p)
            endpoint = "/".join(normalized)

            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=endpoint,
                status_code=response.status_code,
            ).inc()
            REQUEST_LATENCY.labels(
                method=request.method,
                endpoint=endpoint,
            ).observe(duration)
        return response


def metrics_app(wsgi_app):
    """为 WSGI 应用添加 /metrics 端点"""
    if not _metrics_enabled:
        return wsgi_app

    from prometheus_client import make_wsgi_app
    metrics_wsgi = make_wsgi_app()

    def combined_app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == "/metrics":
            return metrics_wsgi(environ, start_response)
        return wsgi_app(environ, start_response)

    return combined_app


# ---------------------------------------------------------------------------
# 缓存指标集成
# ---------------------------------------------------------------------------

def record_cache_hit(cache_type: str):
    """记录缓存命中"""
    CACHE_HITS.labels(cache_type=cache_type).inc()


def record_cache_miss(cache_type: str):
    """记录缓存未命中"""
    CACHE_MISSES.labels(cache_type=cache_type).inc()


# ---------------------------------------------------------------------------
# 指标暴露端点（Flask 蓝图）
# ---------------------------------------------------------------------------

def create_metrics_blueprint():
    """创建 /metrics 端点蓝图"""
    from flask import Blueprint, Response

    bp = Blueprint("metrics", __name__)

    @bp.route("/metrics")
    def prometheus_metrics():
        if _metrics_enabled:
            return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
        return Response("# metrics disabled (prometheus_client not installed)\n", mimetype="text/plain")

    return bp
