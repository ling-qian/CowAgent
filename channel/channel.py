"""
Message sending channel abstract class
"""

from bridge.bridge import Bridge
from bridge.context import Context
from bridge.reply import *
from common.log import logger
from config import conf


class Channel(object):
    channel_type = ""
    NOT_SUPPORT_REPLYTYPE = [ReplyType.VOICE, ReplyType.IMAGE]

    def __init__(self):
        import threading
        self._startup_event = threading.Event()
        self._startup_error = None
        self.cloud_mode = False  # set to True by ChannelManager when running with cloud client

    def startup(self):
        """
        init channel
        """
        raise NotImplementedError

    def report_startup_success(self):
        self._startup_error = None
        self._startup_event.set()

    def report_startup_error(self, error: str):
        self._startup_error = error
        self._startup_event.set()

    def wait_startup(self, timeout: float = 3) -> (bool, str):
        """
        Wait for channel startup result.
        Returns (success: bool, error_msg: str).
        """
        ready = self._startup_event.wait(timeout=timeout)
        if not ready:
            return True, ""
        if self._startup_error:
            return False, self._startup_error
        return True, ""

    def stop(self):
        """
        stop channel gracefully, called before restart
        """
        pass

    def handle_text(self, msg):
        """
        process received msg
        :param msg: message object
        """
        raise NotImplementedError

    # 统一的发送函数，每个Channel自行实现，根据reply的type字段发送不同类型的消息
    def send(self, reply: Reply, context: Context):
        """
        send message to user
        :param msg: message content
        :param receiver: receiver channel account
        :return:
        """
        raise NotImplementedError

    def build_reply_content(self, query, context: Context = None) -> Reply:
        """
        Build reply content, using agent if enabled in config.
        In SaaS mode, routes to tenant-specific Agent via ChatEngine.
        """
        # Check if agent mode is enabled
        use_agent = conf().get("agent", True)

        # --- SaaS multi-tenant routing ---
        # If context contains channel_type + app_id, try to resolve tenant
        tenant_id = None
        if context:
            channel_type = context.get("channel_type") or self.channel_type
            app_id = context.get("app_id")
            if app_id and channel_type:
                tenant_id = self._resolve_tenant(channel_type, app_id)

        if tenant_id:
            try:
                from saas.chat_engine import ChatEngine
                session_id = context.get("session_id") if context else None
                result = ChatEngine.chat(
                    tenant_id=tenant_id,
                    message=query,
                    session_id=session_id,
                )
                # ChatEngine.chat() returns dict with "content" key
                reply_text = result.get("content", "") if isinstance(result, dict) else str(result)
                return Reply(ReplyType.TEXT, reply_text or "")
            except Exception as e:
                logger.error(f"[Channel] SaaS tenant routing failed (tenant={tenant_id}): {e}")
                # Fall through to normal agent mode

        if use_agent:
            try:
                logger.info("[Channel] Using agent mode")

                # Add channel_type to context if not present
                if context and "channel_type" not in context:
                    context["channel_type"] = self.channel_type

                # Read on_event callback injected by the channel (e.g. web SSE)
                on_event = context.get("on_event") if context else None

                # Use agent bridge to handle the query
                return Bridge().fetch_agent_reply(
                    query=query,
                    context=context,
                    on_event=on_event,
                    clear_history=False
                )
            except Exception as e:
                logger.error(f"[Channel] Agent mode failed, fallback to normal mode: {e}")
                # Fallback to normal mode if agent fails
                return Bridge().fetch_reply_content(query, context)
        else:
            # Normal mode
            return Bridge().fetch_reply_content(query, context)

    def _detect_app_id(self):
        """Auto-detect app_id from channel config for SaaS tenant routing.

        Each channel type stores its app_id in a different config key or attribute.
        This method tries common patterns so subclasses don't need to override.
        """
        from config import conf

        # Mapping: channel_type → config key that holds the app_id
        _APP_ID_KEYS = {
            "feishu": "feishu_app_id",
            "dingtalk": "dingtalk_client_id",
            "wechat_mp": "wechat_mp_app_id",
            "wechat_com": "wechatcom_corpid",
            "wechat_kf": "wechat_kf_corpid",
            "wecom_bot": "wecom_bot_key",
            "telegram": "telegram_bot_token",
            "slack": "slack_bot_token",
        }

        # 1. Try config key based on channel_type
        config_key = _APP_ID_KEYS.get(self.channel_type)
        if config_key:
            app_id = conf().get(config_key)
            if app_id:
                return app_id

        # 2. Try common attribute names on the channel instance
        for attr in ("feishu_app_id", "dingtalk_client_id", "corp_id", "app_id"):
            val = getattr(self, attr, None)
            if val:
                return val

        return None

    def _resolve_tenant(self, channel_type: str, app_id: str) -> str:
        """根据 channel_type + app_id 查找绑定的 tenant_id"""
        try:
            from saas.database import IMChannelMapping
            mapping = IMChannelMapping.query.filter_by(
                channel_type=channel_type, app_id=app_id, is_active=True
            ).first()
            if mapping:
                logger.info(f"[Channel] Resolved tenant {mapping.tenant_id} for {channel_type}/{app_id}")
                return mapping.tenant_id
        except Exception as e:
            logger.warning(f"[Channel] Failed to resolve tenant for {channel_type}/{app_id}: {e}")
        return None

    def build_voice_to_text(self, voice_file) -> Reply:
        return Bridge().fetch_voice_to_text(voice_file)

    def build_text_to_voice(self, text) -> Reply:
        return Bridge().fetch_text_to_voice(text)
