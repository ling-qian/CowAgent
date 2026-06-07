# encoding:utf-8
"""
SSO 集成 — OAuth2/OIDC 登录

支持的身份提供商：
- GitHub OAuth2
- Google OAuth2
- 微信开放平台 OAuth2
- 飞书开放平台 OAuth2
- 钉钉 OAuth2
- 通用 OIDC（企业 IdP）

流程：
1. 前端跳转 /api/auth/sso/{provider} → 302 到 IdP 授权页
2. IdP 回调 /api/auth/sso/{provider}/callback
3. 后端用 code 换 token → 获取用户信息 → 创建/关联 User → 返回 API Key
"""

import json
import os
import secrets
import hashlib
import urllib.parse

from flask import Blueprint, request, jsonify, redirect, current_app

from saas.database import db, Tenant, User, ApiKey
from saas.audit import audit_log

sso_bp = Blueprint("sso", __name__)


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)

# ---------------------------------------------------------------------------
# Provider 配置
# ---------------------------------------------------------------------------

PROVIDERS = {
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scope": "read:user user:email",
        "id_field": "id",
        "email_field": "email",
        "name_field": "name",
    },
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scope": "openid email profile",
        "id_field": "id",
        "email_field": "email",
        "name_field": "name",
    },
    "wechat": {
        "authorize_url": "https://open.weixin.qq.com/connect/qrconnect",
        "token_url": "https://api.weixin.qq.com/sns/oauth2/access_token",
        "userinfo_url": "https://api.weixin.qq.com/sns/userinfo",
        "scope": "snsapi_login",
        "id_field": "openid",
        "email_field": "openid",  # 微信不返回邮箱
        "name_field": "nickname",
        # 微信特殊参数
        "token_params_extra": {"grant_type": "authorization_code"},
        "userinfo_lang": "zh_CN",
    },
    "feishu": {
        "authorize_url": "https://open.feishu.cn/open-apis/authen/v1/authorize",
        "token_url": "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
        "userinfo_url": "https://open.feishu.cn/open-apis/authen/v1/user_info",
        "scope": "contact:user.base:readonly",
        "id_field": "open_id",
        "email_field": "email",
        "name_field": "name",
        # 飞书需要 app_access_token
        "app_token_url": "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
    },
    "dingtalk": {
        "authorize_url": "https://login.dingtalk.com/oauth2/auth",
        "token_url": "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
        "userinfo_url": "https://api.dingtalk.com/v1.0/contact/users/me",
        "scope": "openid",
        "id_field": "openId",
        "email_field": "email",
        "name_field": "nick",
        # 钉钉需要先获取 userAccessToken（非标准 OAuth2）
        "token_body_format": "dingtalk",
    },
}


def _get_provider_config(provider):
    """获取 Provider 配置 + 环境变量中的 client_id/secret"""
    if provider not in PROVIDERS:
        return None

    cfg = PROVIDERS[provider].copy()
    prefix = provider.upper()
    cfg["client_id"] = os.environ.get(f"{prefix}_CLIENT_ID", "")
    cfg["client_secret"] = os.environ.get(f"{prefix}_CLIENT_SECRET", "")
    return cfg


# ---------------------------------------------------------------------------
# 状态管理（防 CSRF）
# ---------------------------------------------------------------------------

_state_store = {}  # 生产环境应替换为 Redis


def _generate_state(provider, redirect_uri=None):
    """生成 OAuth state 参数"""
    state = secrets.token_urlsafe(32)
    _state_store[state] = {
        "provider": provider,
        "redirect_uri": redirect_uri,
    }
    return state


def _verify_state(state):
    """验证并消费 state"""
    return _state_store.pop(state, None)


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------

@sso_bp.route("/sso/<provider>", methods=["GET"])
def sso_login(provider):
    """发起 SSO 登录 — 302 重定向到 IdP 授权页"""
    cfg = _get_provider_config(provider)
    if not cfg:
        return jsonify({"error": f"Unsupported provider: {provider}"}), 400

    if not cfg["client_id"]:
        return jsonify({"error": f"Provider {provider} not configured"}), 503

    redirect_uri = request.args.get("redirect_uri", "")
    state = _generate_state(provider, redirect_uri)

    callback_url = f"{request.host_url}api/auth/sso/{provider}/callback"
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": callback_url,
        "scope": cfg["scope"],
        "state": state,
        "response_type": "code",
    }

    # 微信特殊参数
    if provider == "wechat":
        params["appid"] = cfg["client_id"]
        del params["client_id"]
        params.pop("response_type", None)

    # 飞书特殊参数
    if provider == "feishu":
        params["app_id"] = cfg["client_id"]
        del params["client_id"]

    auth_url = f"{cfg['authorize_url']}?{urllib.parse.urlencode(params)}"

    # 微信需要在 URL 末尾加 #wechat_redirect
    if provider == "wechat":
        auth_url += "#wechat_redirect"

    return redirect(auth_url)


@sso_bp.route("/sso/<provider>/callback", methods=["GET"])
def sso_callback(provider):
    """SSO 回调 — 用 code 换 token → 获取用户信息 → 登录/注册"""
    cfg = _get_provider_config(provider)
    if not cfg:
        return jsonify({"error": f"Unsupported provider: {provider}"}), 400

    # 验证 state
    state = request.args.get("state", "")
    state_data = _verify_state(state)
    if not state_data or state_data.get("provider") != provider:
        return jsonify({"error": "Invalid state parameter"}), 400

    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing authorization code"}), 400

    # 用 code 换 access_token
    import urllib.request
    callback_url = f"{request.host_url}api/auth/sso/{provider}/callback"

    if provider == "wechat":
        # 微信：GET 请求 + appid/secret 参数
        token_params = {
            "appid": cfg["client_id"],
            "secret": cfg["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
        }
        token_url = f"{cfg['token_url']}?{urllib.parse.urlencode(token_params)}"
        req = urllib.request.Request(token_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req) as resp:
            token_response = json.loads(resp.read().decode())
        access_token = token_response.get("access_token")
        openid = token_response.get("openid", "")

    elif provider == "feishu":
        # 飞书：先获取 app_access_token，再用 code 换 user_access_token
        app_token_data = json.dumps({
            "app_id": cfg["client_id"],
            "app_secret": cfg["client_secret"],
        }).encode()
        app_req = urllib.request.Request(
            cfg["app_token_url"],
            data=app_token_data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(app_req) as resp:
            app_token_resp = json.loads(resp.read().decode())
        app_access_token = app_token_resp.get("app_access_token")

        token_body = json.dumps({
            "grant_type": "authorization_code",
            "code": code,
        }).encode()
        req = urllib.request.Request(
            cfg["token_url"],
            data=token_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {app_access_token}",
            },
        )
        with urllib.request.urlopen(req) as resp:
            token_response = json.loads(resp.read().decode())
        access_token = token_response.get("access_token")

    elif provider == "dingtalk":
        # 钉钉：先获取 userAccessToken（非标准 OAuth2）
        token_body = json.dumps({
            "clientId": cfg["client_id"],
            "clientSecret": cfg["client_secret"],
            "code": code,
            "grantType": "authorization_code",
        }).encode()
        req = urllib.request.Request(
            cfg["token_url"],
            data=token_body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            token_response = json.loads(resp.read().decode())
        access_token = token_response.get("accessToken")

    else:
        # 标准 OAuth2（GitHub/Google）
        token_data = urllib.parse.urlencode({
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "code": code,
            "redirect_uri": callback_url,
            "grant_type": "authorization_code",
        }).encode()

        headers = {"Accept": "application/json"}
        if provider == "github":
            headers["Accept"] = "application/json"

        req = urllib.request.Request(cfg["token_url"], data=token_data, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                token_response = json.loads(resp.read().decode())
        except Exception as e:
            return jsonify({"error": f"Token exchange failed: {e}"}), 502
        access_token = token_response.get("access_token")
    if not access_token:
        return jsonify({"error": "No access_token in response"}), 502

    # 获取用户信息
    if provider == "wechat":
        # 微信：access_token + openid 参数
        userinfo_params = {
            "access_token": access_token,
            "openid": openid,
            "lang": cfg.get("userinfo_lang", "zh_CN"),
        }
        userinfo_url = f"{cfg['userinfo_url']}?{urllib.parse.urlencode(userinfo_params)}"
        userinfo_req = urllib.request.Request(userinfo_url)
    elif provider == "feishu":
        # 飞书：Bearer token 认证
        userinfo_req = urllib.request.Request(
            cfg["userinfo_url"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
    elif provider == "dingtalk":
        # 钉钉：Bearer userAccessToken
        userinfo_req = urllib.request.Request(
            cfg["userinfo_url"],
            headers={
                "x-acs-dingtalk-access-token": access_token,
            },
        )
    else:
        # 标准 OAuth2
        userinfo_req = urllib.request.Request(
            cfg["userinfo_url"],
            headers={"Authorization": f"Bearer {access_token}"},
        )

    try:
        with urllib.request.urlopen(userinfo_req) as resp:
            userinfo = json.loads(resp.read().decode())
    except Exception as e:
        return jsonify({"error": f"Userinfo fetch failed: {e}"}), 502

    sso_id = str(userinfo.get(cfg["id_field"], ""))
    email = userinfo.get(cfg["email_field"], "")
    name = userinfo.get(cfg["name_field"], "") or userinfo.get("login", "")

    if not sso_id:
        return jsonify({"error": "Cannot extract user ID from provider"}), 502

    # 查找或创建用户
    sso_uid = f"{provider}:{sso_id}"
    user = User.query.filter_by(sso_uid=sso_uid).first()

    if user:
        # 已有用户 — 直接登录
        tenant_id = user.tenant_id
        audit_log(
            action="user.sso_login", resource_type="user", resource_id=user.id,
            detail=f"provider={provider}, sso_uid={sso_uid}",
            tenant_id=tenant_id, ip_address=_client_ip(),
        )
    else:
        # 新用户 — 自动创建租户 + 用户
        slug = f"sso-{provider}-{sso_id[:8]}"
        # 确保 slug 唯一
        while Tenant.query.filter_by(slug=slug).first():
            slug = f"sso-{provider}-{sso_id[:8]}-{secrets.token_hex(2)}"

        tenant = Tenant(
            name=name or slug,
            slug=slug,
            plan="free",
        )
        db.session.add(tenant)
        db.session.flush()

        user = User(
            tenant_id=tenant.id,
            email=email,
            display_name=name,
            role="admin",
            sso_uid=sso_uid,
        )
        db.session.add(user)
        db.session.flush()

        # 自动创建 API Key
        from saas.api.keys import generate_api_key
        raw_key, key_hash, key_prefix = generate_api_key()
        api_key = ApiKey(
            tenant_id=tenant.id,
            name=f"SSO auto ({provider})",
            key_hash=key_hash,
            key_prefix=key_prefix,
        )
        db.session.add(api_key)
        db.session.flush()

        tenant_id = tenant.id

        audit_log(
            action="user.sso_register", resource_type="user", resource_id=user.id,
            detail=f"provider={provider}, email={email}, tenant={slug}",
            tenant_id=tenant_id, ip_address=_client_ip(),
        )

    # 生成临时 API Key（用于本次登录会话）
    from saas.api.keys import generate_api_key
    raw_key, key_hash, key_prefix = generate_api_key()
    session_key = ApiKey(
        tenant_id=tenant_id,
        name=f"Session ({provider})",
        key_hash=key_hash,
        key_prefix=key_prefix,
    )
    db.session.add(session_key)
    db.session.commit()

    # 重定向到前端，携带 API Key
    frontend_redirect = state_data.get("redirect_uri") or "/dashboard"
    separator = "&" if "?" in frontend_redirect else "?"
    return redirect(f"{frontend_redirect}{separator}api_key={raw_key}&tenant_id={tenant_id}")


@sso_bp.route("/sso/providers", methods=["GET"])
def list_providers():
    """列出可用的 SSO Provider"""
    available = []
    for provider, cfg in PROVIDERS.items():
        client_id = os.environ.get(f"{provider.upper()}_CLIENT_ID", "")
        available.append({
            "provider": provider,
            "configured": bool(client_id),
            "name": provider.capitalize(),
        })
    return jsonify({"providers": available})
