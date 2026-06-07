# encoding:utf-8
"""
邮件通知系统

为 SaaS 平台提供邮件通知功能，包括：
- 配额告警（接近/超过配额时通知）
- 账单生成通知
- API Key 即将过期提醒
- 租户状态变更通知

支持 SMTP 和 Resend API 两种发送方式。
未配置邮件服务时，所有通知降级为日志输出。

配置（环境变量或 config.json）：
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=your@gmail.com
    SMTP_PASSWORD=app-password
    SMTP_FROM=noreply@yourdomain.com

    # 或使用 Resend API
    RESEND_API_KEY=re_xxxxx
"""

import json
import os
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from common.log import logger


# ---------------------------------------------------------------------------
# 邮件发送后端
# ---------------------------------------------------------------------------

class EmailBackend:
    """邮件发送后端接口"""

    def send(self, to: str, subject: str, html_body: str) -> bool:
        raise NotImplementedError


class SMTPEmailBackend(EmailBackend):
    """SMTP 邮件发送后端"""

    def __init__(self, host: str, port: int, user: str, password: str,
                 from_addr: str, use_tls: bool = True):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr or user
        self.use_tls = use_tls

    def send(self, to: str, subject: str, html_body: str) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.from_addr
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(self.host, self.port) as server:
                if self.use_tls:
                    server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.from_addr, [to], msg.as_string())

            logger.info(f"[Email] Sent to {to}: {subject}")
            return True
        except Exception as e:
            logger.error(f"[Email] Failed to send to {to}: {e}")
            return False


class ResendEmailBackend(EmailBackend):
    """Resend API 邮件发送后端（https://resend.com）"""

    def __init__(self, api_key: str, from_addr: str = "CowAgent <noreply@yourdomain.com>"):
        self.api_key = api_key
        self.from_addr = from_addr

    def send(self, to: str, subject: str, html_body: str) -> bool:
        try:
            import urllib.request
            import urllib.error

            data = json.dumps({
                "from": self.from_addr,
                "to": [to],
                "subject": subject,
                "html": html_body,
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    logger.info(f"[Email] Sent via Resend to {to}: {subject}")
                    return True
                else:
                    logger.error(f"[Email] Resend returned {resp.status}")
                    return False
        except Exception as e:
            logger.error(f"[Email] Resend failed: {e}")
            return False


class LogEmailBackend(EmailBackend):
    """日志邮件后端（默认，不实际发送邮件）"""

    def send(self, to: str, subject: str, html_body: str) -> bool:
        logger.info(f"[Email-Log] To: {to}, Subject: {subject}")
        return True


# ---------------------------------------------------------------------------
# 全局邮件后端实例
# ---------------------------------------------------------------------------

_backend: Optional[EmailBackend] = None
_backend_lock = threading.Lock()


def _get_backend() -> EmailBackend:
    """获取邮件后端实例（懒初始化）"""
    global _backend
    if _backend is not None:
        return _backend

    with _backend_lock:
        if _backend is not None:
            return _backend

        # 优先使用 Resend API
        resend_key = os.environ.get("RESEND_API_KEY", "")
        if resend_key:
            from_addr = os.environ.get("RESEND_FROM", "CowAgent <noreply@cowagent.dev>")
            _backend = ResendEmailBackend(resend_key, from_addr)
            logger.info("[Email] Using Resend API backend")
            return _backend

        # 其次使用 SMTP
        smtp_host = os.environ.get("SMTP_HOST", "")
        if smtp_host:
            _backend = SMTPEmailBackend(
                host=smtp_host,
                port=int(os.environ.get("SMTP_PORT", "587")),
                user=os.environ.get("SMTP_USER", ""),
                password=os.environ.get("SMTP_PASSWORD", ""),
                from_addr=os.environ.get("SMTP_FROM", ""),
                use_tls=os.environ.get("SMTP_TLS", "true").lower() == "true",
            )
            logger.info(f"[Email] Using SMTP backend: {smtp_host}")
            return _backend

        # 默认降级为日志
        _backend = LogEmailBackend()
        logger.info("[Email] Using log backend (no SMTP/Resend configured)")
        return _backend


# ---------------------------------------------------------------------------
# 邮件模板
# ---------------------------------------------------------------------------

def _quota_alert_html(tenant_name: str, usage_pct: float, plan: str,
                       monthly_calls: int, monthly_limit: int) -> str:
    """配额告警邮件模板"""
    color = "#f5222d" if usage_pct >= 1.0 else "#fa8c16"
    status = "已超限" if usage_pct >= 1.0 else "即将用尽"

    return f"""
    <div style="max-width:600px;margin:0 auto;font-family:sans-serif;">
      <div style="background:{color};color:#fff;padding:20px;text-align:center;">
        <h2>CowAgent 配额{status}告警</h2>
      </div>
      <div style="padding:20px;background:#f9f9f9;">
        <p>尊敬的 <strong>{tenant_name}</strong> 用户：</p>
        <p>您的 CowAgent SaaS 服务配额{status}，请及时关注：</p>
        <table style="width:100%;border-collapse:collapse;margin:16px 0;">
          <tr><td style="padding:8px;border:1px solid #ddd;">当前套餐</td>
              <td style="padding:8px;border:1px solid #ddd;">{plan}</td></tr>
          <tr><td style="padding:8px;border:1px solid #ddd;">本月已用</td>
              <td style="padding:8px;border:1px solid #ddd;">{monthly_calls:,} 次</td></tr>
          <tr><td style="padding:8px;border:1px solid #ddd;">月度配额</td>
              <td style="padding:8px;border:1px solid #ddd;">{monthly_limit:,} 次</td></tr>
          <tr><td style="padding:8px;border:1px solid #ddd;">使用率</td>
              <td style="padding:8px;border:1px solid #ddd;color:{color};font-weight:bold;">
                {usage_pct * 100:.1f}%</td></tr>
        </table>
        <p>建议：</p>
        <ul>
          <li>如需继续使用，请升级到更高级套餐</li>
          <li>检查是否有异常调用，优化 API 使用</li>
        </ul>
      </div>
      <div style="padding:12px;text-align:center;color:#999;font-size:12px;">
        此邮件由 CowAgent SaaS 平台自动发送，请勿回复
      </div>
    </div>
    """


def _invoice_html(tenant_name: str, plan: str, amount: str,
                   period: str, items: list) -> str:
    """账单通知邮件模板"""
    items_html = ""
    for item in items:
        items_html += f"""
        <tr>
          <td style="padding:8px;border:1px solid #ddd;">{item.get('name', '')}</td>
          <td style="padding:8px;border:1px solid #ddd;">{item.get('quantity', 1)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{item.get('amount', '')}</td>
        </tr>"""

    return f"""
    <div style="max-width:600px;margin:0 auto;font-family:sans-serif;">
      <div style="background:#1890ff;color:#fff;padding:20px;text-align:center;">
        <h2>CowAgent 账单通知</h2>
      </div>
      <div style="padding:20px;background:#f9f9f9;">
        <p>尊敬的 <strong>{tenant_name}</strong> 用户：</p>
        <p>您的 {period} 账单已生成：</p>
        <table style="width:100%;border-collapse:collapse;margin:16px 0;">
          <tr style="background:#e6f7ff;">
            <th style="padding:8px;border:1px solid #ddd;">项目</th>
            <th style="padding:8px;border:1px solid #ddd;">数量</th>
            <th style="padding:8px;border:1px solid #ddd;">金额</th>
          </tr>
          {items_html}
        </table>
        <p style="font-size:18px;font-weight:bold;">应付金额：{amount}</p>
      </div>
      <div style="padding:12px;text-align:center;color:#999;font-size:12px;">
        此邮件由 CowAgent SaaS 平台自动发送，请勿回复
      </div>
    </div>
    """


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, html_body: str) -> bool:
    """发送邮件（异步，在后台线程中执行）"""
    def _send():
        _get_backend().send(to, subject, html_body)
    threading.Thread(target=_send, daemon=True).start()
    return True


def send_quota_alert(tenant_id: str, tenant_name: str, usage_pct: float,
                      plan: str, monthly_calls: int, monthly_limit: int,
                      email: Optional[str] = None):
    """发送配额告警邮件"""
    if not email:
        # 从数据库查找通知邮箱
        try:
            from saas.database import Tenant
            import json as _json
            tenant = Tenant.query.filter_by(id=tenant_id).first()
            if tenant and tenant.config_json:
                cfg = _json.loads(tenant.config_json)
                email = cfg.get("notification_email")
        except Exception:
            pass

    if not email:
        logger.debug(f"[Email] No notification email for tenant {tenant_id}, skipping quota alert")
        return

    subject = f"[CowAgent] 配额{'超限' if usage_pct >= 1.0 else '告警'} - {usage_pct * 100:.0f}% 已使用"
    html = _quota_alert_html(tenant_name, usage_pct, plan, monthly_calls, monthly_limit)
    send_email(email, subject, html)


def send_invoice_notification(tenant_id: str, tenant_name: str, plan: str,
                               amount: str, period: str, items: list,
                               email: Optional[str] = None):
    """发送账单通知邮件"""
    if not email:
        try:
            from saas.database import Tenant
            import json as _json
            tenant = Tenant.query.filter_by(id=tenant_id).first()
            if tenant and tenant.config_json:
                cfg = _json.loads(tenant.config_json)
                email = cfg.get("notification_email")
        except Exception:
            pass

    if not email:
        return

    subject = f"[CowAgent] {period} 账单通知 - {amount}"
    html = _invoice_html(tenant_name, plan, amount, period, items)
    send_email(email, subject, html)
