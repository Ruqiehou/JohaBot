"""
缓存管理器 - LRU 缓存实现
用于缓存人设、历史记录、AI 回复等
"""
import time
import threading
from collections import OrderedDict
from typing import Any, Optional, Dict
from functools import wraps


class LRUCache:
    """LRU 缓存实现（支持 per-key TTL，线程安全）"""
    
    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.cache: OrderedDict[str, Any] = OrderedDict()
        self.timestamps: Dict[str, float] = {}
        self.ttls: Dict[str, int] = {}
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值（自动检查 TTL 过期）"""
        with self._lock:
            if key in self.cache:
                # 检查是否已过期
                ttl = self.ttls.get(key)
                if ttl is not None and (time.time() - self.timestamps[key]) > ttl:
                    self.delete(key)
                    return None
                # 更新访问顺序
                self.cache.move_to_end(key)
                # 带 TTL 的条目使用绝对过期时间，不因访问而刷新
                if ttl is None:
                    self.timestamps[key] = time.time()
                return self.cache[key]
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存值"""
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                if len(self.cache) >= self.capacity:
                    # 删除最旧的项
                    oldest = next(iter(self.cache))
                    del self.cache[oldest]
                    del self.timestamps[oldest]
                    self.ttls.pop(oldest, None)
            
            self.cache[key] = value
            self.timestamps[key] = time.time()
            if ttl is not None:
                self.ttls[key] = ttl
            else:
                self.ttls.pop(key, None)
    
    def delete(self, key: str) -> bool:
        """删除缓存值"""
        with self._lock:
            if key in self.cache:
                del self.cache[key]
                del self.timestamps[key]
                self.ttls.pop(key, None)
                return True
            return False
    
    def clear(self) -> None:
        """清空缓存"""
        with self._lock:
            self.cache.clear()
            self.timestamps.clear()
            self.ttls.clear()
    
    def cleanup_expired(self, max_age: int = 3600) -> int:
        """清理过期缓存，返回清理数量"""
        with self._lock:
            now = time.time()
            expired_keys = [
                k for k, v in self.timestamps.items()
                if now - v > max_age
            ]
            for key in expired_keys:
                self.delete(key)
            return len(expired_keys)
    
    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self.cache
    
    def __len__(self) -> int:
        with self._lock:
            return len(self.cache)


# 全局缓存实例
persona_cache = LRUCache(capacity=50)  # 人设缓存
history_cache = LRUCache(capacity=20)  # 历史记录缓存（每个用户）
response_cache = LRUCache(capacity=200)  # AI 回复缓存


def cache_result(cache_instance: LRUCache, ttl: int = 300):
    """缓存装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            key = f"{func.__name__}:{str(args)}:{str(sorted(kwargs.items()))}"
            
            # 尝试从缓存获取
            cached = cache_instance.get(key)
            if cached is not None:
                return cached
            
            # 执行函数并缓存结果
            result = func(*args, **kwargs)
            cache_instance.set(key, result, ttl=ttl)
            return result
        return wrapper
    return decorator