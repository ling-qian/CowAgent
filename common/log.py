import io
import logging
import sys


class TenantLogFilter(logging.Filter):
    """日志过滤器：saas_mode=True 时在日志消息前添加 [tenant:xxx] 前缀"""

    def filter(self, record):
        try:
            from common.tenant import current_tenant_id
            from config import conf
            if conf().get("saas_mode", False):
                tid = current_tenant_id()
                if tid:
                    record.msg = f"[tenant:{tid}] {record.msg}"
        except Exception:
            pass
        return True


def _reset_logger(log):
    for handler in log.handlers:
        handler.close()
        log.removeHandler(handler)
        del handler
    log.handlers.clear()
    log.propagate = False
    stdout = sys.stdout
    if hasattr(stdout, "buffer"):
        stdout = io.TextIOWrapper(stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    console_handle = logging.StreamHandler(stdout)
    console_handle.setFormatter(
        logging.Formatter(
            "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handle = logging.FileHandler("run.log", encoding="utf-8")
    file_handle.setFormatter(
        logging.Formatter(
            "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    # 添加租户日志过滤器
    tenant_filter = TenantLogFilter()
    console_handle.addFilter(tenant_filter)
    file_handle.addFilter(tenant_filter)
    log.addHandler(file_handle)
    log.addHandler(console_handle)


def _get_logger():
    log = logging.getLogger("log")
    _reset_logger(log)
    log.setLevel(logging.INFO)
    return log


# 日志句柄
logger = _get_logger()
