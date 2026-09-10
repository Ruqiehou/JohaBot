"""
群组状态追踪器 v2.0 - JSON 持久化版
实时追踪每个群的消息频率、活跃话题、上下文等信息
重启后恢复状态计数和话题数据
"""

import time
import re
import json
import os
import threading
from collections import deque, Counter
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from joha.config.paths import GROUP_STATES_FILE as GROUP_STATE_FILE


@dataclass
class GroupState:
    group_id: str
    message_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    bot_reply_buffer: deque = field(default_factory=lambda: deque(maxlen=50))
    user_message_counts: Dict[str, int] = field(default_factory=dict)
    last_bot_msg: str = ""
    last_msg_from_bot: bool = False
    total_messages: int = 0
    bot_replies: int = 0
    positive_feedbacks: int = 0
    negative_feedbacks: int = 0
    last_active_ts: float = 0.0
    learned_topics: Counter = field(default_factory=Counter)
    last_topic_update: float = 0.0
    _db_sync_count: int = 0
    
    # 持久化话题的最大数量，超出后仅保留计数最高的
    MAX_PERSISTENT_TOPICS: int = 200

    # 用于JSON序列化的字段
    persistent_topics: Dict[str, int] = field(default_factory=dict)

    @property
    def msg_per_minute(self) -> float:
        now = time.time()
        recent = [ts for ts, _ in self.message_buffer if now - ts < 60]
        return len(recent)

    @property
    def msg_per_5min(self) -> float:
        now = time.time()
        recent = [ts for ts, _ in self.message_buffer if now - ts < 300]
        return len(recent) / 5.0

    @property
    def approval_rate(self) -> float:
        total = self.positive_feedbacks + self.negative_feedbacks
        if total == 0:
            return 0.5
        return self.positive_feedbacks / total

    @property
    def is_active_conversation(self) -> bool:
        recent = list(self.message_buffer)[-5:]
        bot_msgs = sum(1 for _, meta in recent if meta.get("is_bot", False))
        return bot_msgs == 0 and len(recent) >= 3

    def record_message(self, user_id: str, text: str, is_bot: bool = False):
        now = time.time()
        self.message_buffer.append((now, {
            "user_id": user_id,
            "text": text,
            "is_bot": is_bot,
        }))
        self.total_messages += 1
        self.last_active_ts = now
        self.last_msg_from_bot = is_bot
        if is_bot:
            self.last_bot_msg = text
            self.bot_replies += 1
        else:
            self.user_message_counts[user_id] = self.user_message_counts.get(user_id, 0) + 1

        self._db_sync_count += 1
        # 注意：实际保存在 GroupStateManager.record_message 中处理

    def record_feedback(self, positive: bool = True):
        if positive:
            self.positive_feedbacks += 1
        else:
            self.negative_feedbacks += 1
        # 注意：实际保存在 GroupStateManager.record_feedback 中处理

    def get_top_topics(self, top_k: int = 10) -> List[Tuple[str, int]]:
        combined = Counter(self.persistent_topics)
        combined.update(self.learned_topics)
        return combined.most_common(top_k)

    def update_topics(self, text: str):
        words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        for w in words:
            if len(w) >= 2:
                self.learned_topics[w] += 1

        self.last_topic_update = time.time()
        self._db_sync_count += 1
        if self._db_sync_count % 15 == 0:
            # 将学习的话题合并到持久化话题中
            self._sync_topics_to_file()
    
    def to_dict(self) -> Dict:
        """转换为字典用于JSON序列化"""
        return {
            "group_id": self.group_id,
            "total_messages": self.total_messages,
            "bot_replies": self.bot_replies,
            "positive_feedbacks": self.positive_feedbacks,
            "negative_feedbacks": self.negative_feedbacks,
            "last_bot_msg": self.last_bot_msg,
            "last_msg_from_bot": self.last_msg_from_bot,
            "last_active_ts": self.last_active_ts,
            "persistent_topics": dict(self.persistent_topics),
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "GroupState":
        """从字典创建GroupState"""
        state = cls(group_id=data.get("group_id", ""))
        state.total_messages = data.get("total_messages", 0)
        state.bot_replies = data.get("bot_replies", 0)
        state.positive_feedbacks = data.get("positive_feedbacks", 0)
        state.negative_feedbacks = data.get("negative_feedbacks", 0)
        state.last_bot_msg = data.get("last_bot_msg", "")
        state.last_msg_from_bot = data.get("last_msg_from_bot", False)
        state.last_active_ts = data.get("last_active_ts", 0.0)
        state.persistent_topics = data.get("persistent_topics", {})
        # learned_topics 仅保存本次运行新增的话题，避免与持久化数据重复计数
        state.learned_topics = Counter()
        return state

    def _sync_to_file(self):
        """同步到文件（由GroupStateManager统一保存）"""
        # 标记为需要保存，实际保存在GroupStateManager中处理
        pass

    def _sync_topics_to_file(self):
        """同步话题到持久化存储"""
        # 将当前学习的话题合并到持久化话题中
        for topic, count in self.learned_topics.items():
            self.persistent_topics[topic] = self.persistent_topics.get(topic, 0) + count
        self.learned_topics.clear()

        # 剪枝：仅保留计数最高的若干话题，避免无界增长
        if len(self.persistent_topics) > self.MAX_PERSISTENT_TOPICS:
            top = sorted(
                self.persistent_topics.items(),
                key=lambda kv: kv[1],
                reverse=True,
            )[:self.MAX_PERSISTENT_TOPICS]
            self.persistent_topics = dict(top)


class GroupStateManager:

    def __init__(self):
        self._states: Dict[str, GroupState] = {}
        # 可重入锁：既保护 _states 的并发读写，也串行化文件读写
        self._lock = threading.RLock()
        self._load_from_file()
    
    def _load_from_file(self):
        """从文件加载群组状态"""
        with self._lock:
            try:
                if os.path.exists(GROUP_STATE_FILE):
                    with open(GROUP_STATE_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    for group_id, state_data in data.items():
                        self._states[group_id] = GroupState.from_dict(state_data)

                    if self._states:
                        from joha.config.logger import tprint
                        tprint("info", f"[GroupState] 已恢复 {len(self._states)} 个群组状态")
            except Exception as e:
                from joha.config.logger import tprint
                tprint("error", f"[GroupState] 加载群组状态失败: {e}")

    def _save_to_file(self):
        """保存所有群组状态到文件"""
        with self._lock:
            try:
                storage_dir = os.path.dirname(GROUP_STATE_FILE)
                os.makedirs(storage_dir, exist_ok=True)

                data = {}
                for group_id, state in self._states.items():
                    data[group_id] = state.to_dict()

                with open(GROUP_STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                from joha.config.logger import tprint
                tprint("error", f"[GroupState] 保存群组状态失败: {e}")

    def get(self, group_id: str) -> GroupState:
        with self._lock:
            if group_id not in self._states:
                state = GroupState(group_id=group_id)
                self._states[group_id] = state
            return self._states[group_id]

    def record_message(self, group_id: str, user_id: str, text: str, is_bot: bool = False):
        state = self.get(group_id)
        state.record_message(user_id, text, is_bot)
        if not is_bot:
            state.update_topics(text)
        
        # 每20条消息保存一次
        if state._db_sync_count % 20 == 0:
            self._save_to_file()

    def record_bot_reply(self, group_id: str, text: str):
        self.record_message(group_id, "bot", text, is_bot=True)

    def record_feedback(self, group_id: str, positive: bool = True):
        self.get(group_id).record_feedback(positive)
        self._save_to_file()  # 反馈后立即保存

    def get_context_summary(self, group_id: str, n: int = 5) -> str:
        state = self.get(group_id)
        with self._lock:
            recent = list(state.message_buffer)[-n:]
            lines = []
            for _, meta in recent:
                prefix = "[Bot]" if meta.get("is_bot") else f"[U{meta['user_id'][-4:]}]"
                lines.append(f"{prefix}: {meta['text'][:30]}")
            return "\n".join(lines)

    def get_stats(self) -> Dict:
        with self._lock:
            return {
                "total_groups": len(self._states),
                "total_messages": sum(s.total_messages for s in self._states.values()),
                "total_bot_replies": sum(s.bot_replies for s in self._states.values()),
                "avg_msg_per_min": sum(s.msg_per_minute for s in self._states.values()) / max(len(self._states), 1),
            }


group_state_manager = GroupStateManager()
