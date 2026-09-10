import time
import json
import os
import threading
import functools
from dataclasses import dataclass, asdict
from typing import Dict, Optional
from joha.config.logger import johalog_logger
from joha.config.paths import STORAGE_ROOT, USER_PROFILES_FILE


def _locked(method):
    """使用实例的 _lock 串行化方法调用（可重入）"""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper

PROFILES_DIR = STORAGE_ROOT
PROFILES_FILE = USER_PROFILES_FILE


@dataclass
class UserProfile:
    user_id: str
    total_interactions: int = 0
    positive_feedbacks: int = 0
    last_interaction_ts: float = 0.0
    is_blocked: bool = False
    is_vip: bool = False

    def score(self) -> float:
        if self.is_blocked:
            return -5.0
        if self.is_vip:
            return 1.5

        score = 0.0

        if self.total_interactions > 0:
            quality = self.positive_feedbacks / self.total_interactions
            score += (quality - 0.5) * 1.0
        else:
            score += 0.3

        gap = time.time() - self.last_interaction_ts
        if gap < 10:
            score -= 1.5
        elif gap < 60:
            score -= 0.5

        return score

    def to_dict(self) -> Dict:
        """转换为字典用于JSON序列化"""
        return {
            "user_id": self.user_id,
            "total_interactions": self.total_interactions,
            "positive_feedbacks": self.positive_feedbacks,
            "last_interaction_ts": self.last_interaction_ts,
            "is_blocked": self.is_blocked,
            "is_vip": self.is_vip,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "UserProfile":
        """从字典创建UserProfile"""
        return cls(
            user_id=data.get("user_id", ""),
            total_interactions=data.get("total_interactions", 0),
            positive_feedbacks=data.get("positive_feedbacks", 0),
            last_interaction_ts=data.get("last_interaction_ts", 0.0),
            is_blocked=bool(data.get("is_blocked", False)),
            is_vip=bool(data.get("is_vip", False)),
        )


class UserProfileManager:

    # 两次落盘之间的最小间隔（秒），避免高频写盘
    SAVE_INTERVAL: float = 30.0
    # 缓存上限，超出后淘汰最久未互动且非拉黑/VIP 的用户
    MAX_CACHE_SIZE: int = 5000

    def __init__(self):
        self._cache: Dict[str, UserProfile] = {}
        self._dirty: set = set()
        self._last_save_ts: float = 0.0
        self._lock = threading.RLock()
        self._load_from_disk()
    
    @_locked
    def _load_from_disk(self):
        """从磁盘加载用户画像数据"""
        try:
            if os.path.exists(PROFILES_FILE):
                with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                for user_id, profile_data in data.items():
                    self._cache[user_id] = UserProfile.from_dict(profile_data)
                
                johalog_logger.info(f"已加载 {len(self._cache)} 个用户画像")
        except Exception as e:
            johalog_logger.error(f"加载用户画像失败: {e}")
    
    @_locked
    def _save_to_disk(self):
        """保存所有用户画像到磁盘"""
        try:
            data = {}
            for user_id, profile in self._cache.items():
                data[user_id] = profile.to_dict()
            
            os.makedirs(PROFILES_DIR, exist_ok=True)
            with open(PROFILES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            self._dirty.clear()
            self._last_save_ts = time.time()
        except Exception as e:
            johalog_logger.error(f"保存用户画像失败: {e}")
    
    @_locked
    def _mark_dirty(self, user_id: str):
        """标记用户画像为待保存"""
        self._dirty.add(user_id)

    @_locked
    def _evict_if_needed(self):
        """缓存超限时淘汰最久未互动且非拉黑/VIP 的用户"""
        if len(self._cache) <= self.MAX_CACHE_SIZE:
            return
        # 先落盘，避免淘汰掉未保存的数据
        self._save_to_disk()
        candidates = [
            uid for uid, p in self._cache.items()
            if not p.is_blocked and not p.is_vip
        ]
        candidates.sort(key=lambda uid: self._cache[uid].last_interaction_ts)
        overflow = len(self._cache) - self.MAX_CACHE_SIZE
        for uid in candidates[:overflow]:
            self._cache.pop(uid, None)

    @_locked
    def get(self, user_id: str) -> UserProfile:
        if user_id in self._cache:
            return self._cache[user_id]
        
        # 如果缓存中没有，创建新的默认画像
        profile = UserProfile(user_id=user_id)
        self._cache[user_id] = profile
        self._mark_dirty(user_id)
        self._evict_if_needed()
        return profile

    @_locked
    def record_interaction(self, user_id: str, positive: bool = False):
        p = self.get(user_id)
        p.total_interactions += 1
        p.last_interaction_ts = time.time()
        if positive:
            p.positive_feedbacks += 1
        self._mark_dirty(user_id)

    @_locked
    def set_blocked(self, user_id: str, blocked: bool):
        self.get(user_id).is_blocked = blocked
        self._mark_dirty(user_id)

    @_locked
    def set_vip(self, user_id: str, vip: bool):
        self.get(user_id).is_vip = vip
        self._mark_dirty(user_id)
    
    @_locked
    def save_all(self, force: bool = False):
        """保存所有待保存的用户画像（默认按 SAVE_INTERVAL 节流）

        Args:
            force: 为 True 时忽略节流间隔立即保存
        """
        if not self._dirty:
            return
        if not force and (time.time() - self._last_save_ts) < self.SAVE_INTERVAL:
            return
        self._save_to_disk()


user_profile_manager = UserProfileManager()
