"""
用户风格学习器 - JSON 存储版
分析并学习用户的说话方式，支持更丰富的用户画像
"""
import re
import json
import os
import threading
import functools
from typing import Dict, Any, List, Optional
from collections import Counter
from joha.config.logger import johalog_logger
from joha.config.paths import STYLES_DIR


def _locked(method):
    """使用实例的 _lock 串行化方法调用（可重入）"""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class StyleLearner:

    def __init__(self):
        self.styles: Dict[str, Dict[str, Any]] = {}
        self._dirty: set = set()
        self._lock = threading.RLock()
        os.makedirs(STYLES_DIR, exist_ok=True)
        self._load_all_from_disk()
        count = len(self.styles)
        johalog_logger.info(f"已加载 {count} 个用户风格记录")
    
    def _user_file_path(self, userid: str) -> str:
        """获取用户风格文件路径"""
        safe_id = str(userid).replace("/", "_").replace("\\", "_")
        return os.path.join(STYLES_DIR, f"{safe_id}.json")
    
    def _load_all_from_disk(self):
        """从磁盘加载所有用户风格"""
        try:
            for filename in os.listdir(STYLES_DIR):
                if not filename.endswith(".json"):
                    continue
                userid = filename[:-5]  # 移除 .json 后缀
                self._load_user_from_file(userid)
        except Exception as e:
            johalog_logger.error(f"加载风格数据失败: {e}")
    
    @_locked
    def _load_user_from_file(self, userid: str) -> Optional[Dict]:
        """从文件加载单个用户风格"""
        path = self._user_file_path(userid)
        if not os.path.exists(path):
            return None
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if not isinstance(data, dict):
                raise ValueError("风格文件内容不是字典")
            
            # 补全缺失字段/修正类型，兼容旧版本或手工修改的文件
            normalized = self._default_style()
            if isinstance(data.get("message_count"), int):
                normalized["message_count"] = data["message_count"]
            if isinstance(data.get("avg_length"), (int, float)):
                normalized["avg_length"] = float(data["avg_length"])
            if isinstance(data.get("emoji_usage"), (int, float)):
                normalized["emoji_usage"] = float(data["emoji_usage"])
            if isinstance(data.get("common_particles"), list):
                normalized["common_particles"] = data["common_particles"]
            if isinstance(data.get("sample_messages"), list):
                normalized["sample_messages"] = data["sample_messages"]
            if isinstance(data.get("sentence_types"), dict):
                normalized["sentence_types"] = Counter(data["sentence_types"])
            
            self.styles[userid] = normalized
            return normalized
        except Exception as e:
            johalog_logger.error(f"读取用户 {userid} 风格文件失败: {e}")
            return None

    @staticmethod
    def _default_style() -> Dict[str, Any]:
        """返回空的用户风格结构"""
        return {
            "message_count": 0,
            "avg_length": 0,
            "emoji_usage": 0.0,
            "common_particles": [],
            "sentence_types": Counter(),
            "sample_messages": [],
        }
    
    @_locked
    def _save_user_to_file(self, userid: str):
        """保存单个用户风格到文件"""
        if userid not in self.styles:
            return
        
        try:
            style = self.styles[userid].copy()
            # 转换 Counter 为普通字典以便 JSON 序列化
            if "sentence_types" in style and isinstance(style["sentence_types"], Counter):
                style["sentence_types"] = dict(style["sentence_types"])
            
            path = self._user_file_path(userid)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(style, f, ensure_ascii=False, indent=2)
            
            self._dirty.discard(userid)
        except Exception as e:
            johalog_logger.error(f"保存用户 {userid} 风格失败: {e}")

    @_locked
    def save_all(self):
        """保存所有待保存的用户风格"""
        for userid in list(self._dirty):
            self._save_user_to_file(userid)

    def analyze_message(self, message: str) -> Dict[str, Any]:
        features = {
            "length": len(message),
            "has_emoji": bool(re.search(
                r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF'
                r'\U0001F680-\U0001F6FF\u2600-\u26FF\u2700-\u27BF]',
                message
            )),
            "has_question": '?' in message or '？' in message,
            "has_exclamation": '!' in message or '！' in message,
            "lowercase_ratio": sum(1 for c in message if c.islower()) / max(len(message), 1),
            "avg_word_length": sum(len(word) for word in message.split()) / max(len(message.split()), 1),
        }

        modal_particles = ['嗯', '啊', '吧', '呢', '嘛', '哦', '呀', '哈', '嘿嘿', '哈哈']
        features["modal_particles"] = [p for p in modal_particles if p in message]

        if message.endswith('...') or message.endswith('…'):
            features["sentence_type"] = "trailing"
        elif features["has_question"]:
            features["sentence_type"] = "question"
        elif features["has_exclamation"]:
            features["sentence_type"] = "exclamation"
        else:
            features["sentence_type"] = "statement"

        return features

    @_locked
    def learn_from_message(self, userid: str, message: str):
        if userid not in self.styles:
            self._load_user_from_file(userid)

        if userid not in self.styles:
            self.styles[userid] = self._default_style()

        user_style = self.styles[userid]
        features = self.analyze_message(message)

        user_style["message_count"] += 1
        n = user_style["message_count"]

        user_style["avg_length"] = (user_style["avg_length"] * (n - 1) + features["length"]) / n
        user_style["emoji_usage"] = (
            user_style["emoji_usage"] * (n - 1) + (1 if features["has_emoji"] else 0)
        ) / n

        for particle in features["modal_particles"]:
            if particle not in user_style["common_particles"]:
                user_style["common_particles"].append(particle)

        user_style["sentence_types"][features["sentence_type"]] += 1

        user_style["sample_messages"].append(message)
        if len(user_style["sample_messages"]) > 10:
            user_style["sample_messages"] = user_style["sample_messages"][-10:]

        # 每10条消息保存到磁盘
        if n % 10 == 0:
            self._save_user_to_file(userid)
        else:
            self._dirty.add(userid)

        johalog_logger.debug(f"[风格学习] 用户 {userid} 已学习 {n} 条消息")

    @_locked
    def get_user_style_prompt(self, userid: str) -> str:
        if userid not in self.styles:
            self._load_user_from_file(userid)

        if userid not in self.styles or self.styles[userid]["message_count"] < 3:
            return ""

        user_style = self.styles[userid]
        style_parts = []

        avg_len = user_style["avg_length"]
        if avg_len < 10:
            style_parts.append("回复很短，通常10字以内")
        elif avg_len < 30:
            style_parts.append("回复简短，一般10-30字")
        else:
            style_parts.append("回复较长")

        if user_style["emoji_usage"] > 0.3:
            style_parts.append("经常使用表情符号")
        elif user_style["emoji_usage"] < 0.1:
            style_parts.append("很少用表情")

        if user_style["common_particles"]:
            particles = '、'.join(user_style["common_particles"][:5])
            style_parts.append(f"喜欢用语气词：{particles}")

        if user_style["sentence_types"]:
            most_common = user_style["sentence_types"].most_common(1)[0][0]
            type_map = {
                "question": "喜欢提问",
                "exclamation": "语气较强",
                "trailing": "说话留有余地",
                "statement": "陈述为主"
            }
            style_parts.append(type_map.get(most_common, ""))

        if style_parts:
            return f"模仿对方说话风格：{'；'.join(style_parts)}。"
        return ""

    @_locked
    def get_sample_messages(self, userid: str, count: int = 3) -> List[str]:
        if userid not in self.styles:
            self._load_user_from_file(userid)
        if userid not in self.styles:
            return []
        return self.styles[userid]["sample_messages"][-count:]

    @_locked
    def clear_user_style(self, userid: str):
        if userid in self.styles:
            del self.styles[userid]
        
        # 删除文件
        path = self._user_file_path(userid)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                johalog_logger.error(f"删除用户 {userid} 风格文件失败: {e}")
        
        self._dirty.discard(userid)
        johalog_logger.info(f"已清除用户 {userid} 的风格数据")

    @_locked
    def get_user_count(self) -> int:
        return len(self.styles)


style_learner = StyleLearner()
