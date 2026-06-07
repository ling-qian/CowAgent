def singleton(cls):
    instances = {}

    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    # 保留原始类引用，允许 BridgeManager 等绕过单例创建新实例
    get_instance.__wrapped__ = cls
    return get_instance
