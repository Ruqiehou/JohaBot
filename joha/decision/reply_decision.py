"""
回复决策模型 v3.0
基于 Logit 累加 + Sigmoid 概率化 + 场景阈值判断
所有可调参数集中到 config.json → reply_decision 段
"""
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Dict, List, Set
from collections import Counter

from joha.config.logger import tprint
from joha.managers.user_profile import UserProfile, user_profile_manager
from joha.decision.cooldown import CooldownManager, cooldown_manager
from joha.decision.group_state import group_state_manager


CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "reply_decision.json"


@dataclass(frozen=True)
class ReplyConfig:
    """回复决策配置（每次调用懒加载最新值）"""

    _data: Dict[str, Any] = None

    def _load(self) -> Dict[str, Any]:
        """懒加载配置文件"""
        if ReplyConfig._data is None:
            try:
                if CONFIG_FILE.exists():
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        ReplyConfig._data = json.load(f)
                else:
                    tprint("warning", f"[ReplyConfig] 配置文件不存在: {CONFIG_FILE}")
                    ReplyConfig._data = {}
            except Exception as e:
                tprint("warning", f"[ReplyConfig] 加载失败: {e}")
                ReplyConfig._data = {}
        return ReplyConfig._data

    def reload(self) -> None:
        """强制重新加载配置"""
        ReplyConfig._data = None

    @property
    def bot_nicknames(self) -> set:
        return set(self._load().get("bot_nicknames", ["bot", "机器人"]))

    @property
    def feedback_weights(self) -> Dict[str, float]:
        return self._load().get("feedback_weights", {})

    @property
    def thresholds(self) -> Dict[str, float]:
        return self._load().get("thresholds", {})

    @property
    def group_dynamic(self) -> Dict[str, float]:
        return self._load().get("group_dynamic", {})

    @property
    def length_bonuses(self) -> Dict[str, float]:
        return self._load().get("length_bonuses", {})

    @property
    def spam_detection(self) -> Dict[str, float]:
        return self._load().get("spam_detection", {})

    @property
    def intent_feedback_mapping(self) -> Dict[str, str]:
        return self._load().get("intent_feedback_mapping", {})

    @property
    def threshold_adjustments(self) -> Dict[str, float]:
        return self._load().get("threshold_adjustments", {})

    def __getattr__(self, name):
        return self._load().get(name)


reply_cfg = ReplyConfig()

INTENT_PATTERNS = {
    "question": [
        r'[?？]', r'为什么', r'为啥', r'为何', r'怎么', r'怎样', r'如何',
        r'是什么', r'什么是', r'哪个', r'哪里', r'吗$', r'呢$', r'吧$',
        r'有没有', r'是不是', r'能不能', r'可不可以', r'谁', r'什么时候',
        r'多久', r'多少', r'怎么办', r'怎么样', r'如何做', r'好不好',
        r'行不行', r'对不对',
    ],
    "command": [
        r'^\s*[#!/]', r'^\s*(给我|帮我|请|把)', r'(计算|翻译|搜索|查一下)',
        r'(写|写个|写一个|生成|创建)', r'(设置|配置|修改|切换)',
    ],
    "emotion": [
        r'(开心|高兴|快乐|爽|棒|赞|牛|厉害|太强了)', r'(难过|伤心|痛苦|委屈|哭|泪)',
        r'(生气|愤怒|烦|郁闷|无语|草|卧槽|他妈)', r'(无聊|寂寞|孤单|累|困|饿)',
        r'(爱|喜欢|想你了|抱抱|亲亲)', r'(哈哈哈|哈哈|呵呵|嘿嘿|嘻嘻)',
        r'(加油|冲|坚持|努力|奥利给)', r'(害怕|恐怖|吓人|惊悚)',
    ],
    "spam": [
        r'^[.\.\n\s]{3,}$', r'^(6{3,}|9{3,}|1{3,}|0{3,})$',
        r'^[啊哦嗯哼]{3,}$', r'^[\s]{5,}$',
    ],
}

_FEEDBACK_ADJUSTMENTS = Counter()


@dataclass
class MessageContext:
    text: str
    user_id: str
    group_id: str = ""
    is_at_bot: bool = False
    reply_to_bot: bool = False
    is_pure_media: bool = False
    is_private: bool = False
    group_msg_per_minute: float = 5.0
    group_msg_per_5min: float = 5.0
    group_approval_rate: float = 0.5
    active_human_conversation: bool = False
    last_msg_from_bot: bool = False
    last_bot_msg: str = ""
    contains_sensitive: bool = False
    user_rate_limited: bool = False
    intent: str = "chat"
    intent_confidence: float = 0.0


def _detect_intent(text: str) -> tuple:
    # 斜杠命令一律视为 command 意图，不进行规则匹配
    if text.startswith('/'):
        return "command", 1.0
    text_lower = text.lower().strip()
    if not text_lower:
        return "chat", 0.0

    scores = Counter()
    for intent, patterns in INTENT_PATTERNS.items():
        for p in patterns:
            if re.search(p, text_lower):
                scores[intent] += 1

    if not scores:
        return "chat", 0.3

    mult = reply_cfg.intent_pattern_multiplier
    best = scores.most_common(1)[0]
    confidence = min(best[1] * mult, 1.0)
    return best[0], confidence


def _is_continuation(text: str, last_bot_msg: str) -> float:
    if not last_bot_msg:
        return 0.0
    last_words = set(re.findall(r'[\u4e00-\u9fff\w]{2,}', last_bot_msg.lower()))
    current_words = set(re.findall(r'[\u4e00-\u9fff\w]{2,}', text.lower()))
    if not last_words or not current_words:
        return 0.0
    overlap = len(last_words & current_words)
    return min(overlap * reply_cfg.continuation_coefficient, 1.0)


def _bot_nickname_in(text: str) -> bool:
    return any(n in text.lower() for n in reply_cfg.bot_nicknames)


def _topic_relevance(text: str, group_id: str) -> float:
    if not group_id:
        return 0.0
    state = group_state_manager.get(group_id)
    top_topics = state.get_top_topics(20)
    if not top_topics:
        return 0.0
    text_lower = text.lower()
    matched = sum(1 for topic, _ in top_topics if topic in text_lower)
    return min(matched * reply_cfg.topic_relevance_coefficient, 1.0)


def _compute_spam_score(text: str) -> float:
    if not text:
        return 0.0
    sd = reply_cfg.spam_detection
    score = 0.0
    char_diversity = len(set(text)) / max(len(text), 1)
    if char_diversity < sd["low_char_diversity_threshold"]:
        score += sd["low_char_diversity_score"]
    elif char_diversity < sd["med_char_diversity_threshold"]:
        score += sd["med_char_diversity_score"]
    if re.search(r'(.)\1{4,}', text):
        score += sd["repeat_detect_score"]
    if len(text) <= 3 and re.match(r'^[啊哦嗯哼6\.\s]+$', text):
        score += sd["nonsense_score"]
    if re.match(r'^[\d\.\n\s,，!！?？]+$', text):
        score += sd["punctuation_only_score"]
    return min(score, 1.0)


def _group_dynamic_score(ctx: MessageContext) -> float:
    gd = reply_cfg.group_dynamic
    score = 0.0
    mpm = ctx.group_msg_per_minute
    if mpm > gd["very_busy_mpm"]:
        score += gd["very_busy_score"]
    elif mpm > gd["busy_mpm"]:
        score += gd["busy_score"]
    elif mpm < gd["dead_mpm"]:
        score += gd["dead_score"]
    elif mpm < gd["quiet_mpm"]:
        score += gd["quiet_score"]

    ar = ctx.group_approval_rate
    if ar > gd["high_approval_rate"]:
        score += gd["high_approval_score"]
    elif ar < gd["low_approval_rate"]:
        score += gd["low_approval_score"]

    if ctx.active_human_conversation:
        score += gd["active_human_score"]
    return score


def _get_adjusted_weight(key: str) -> float:
    base = reply_cfg.feedback_weights.get(key, 0.0)
    adj = _FEEDBACK_ADJUSTMENTS.get(key, 0.0)
    limit = abs(base) * reply_cfg.feedback_adjustment_ratio
    return base + max(-limit, min(limit, adj))


def apply_feedback(intent: str, positive: bool, magnitude: float = 0.1):
    mapping = reply_cfg.intent_feedback_mapping
    key = mapping.get(intent, "topic_relevance")
    delta = magnitude if positive else -magnitude
    _FEEDBACK_ADJUSTMENTS[key] += delta
    fm = reply_cfg.feedback_side_multiplier
    if positive:
        _FEEDBACK_ADJUSTMENTS["at_bot"] += magnitude * fm
        _FEEDBACK_ADJUSTMENTS["reply_to_bot"] += magnitude * fm
    else:
        _FEEDBACK_ADJUSTMENTS["active_human"] -= magnitude * fm
        _FEEDBACK_ADJUSTMENTS["last_from_bot"] -= magnitude * fm


def compute_reply_prob(ctx: MessageContext, cooldown: CooldownManager = cooldown_manager) -> float:
    if ctx.contains_sensitive:
        return 0.0

    profile = user_profile_manager.get(ctx.user_id)
    if profile.is_blocked:
        return 0.0

    ctx.intent, ctx.intent_confidence = _detect_intent(ctx.text)

    sd = reply_cfg.spam_detection
    if ctx.intent == "spam" and ctx.intent_confidence > sd["block_intent_confidence"]:
        return 0.0

    logit = 0.0

    if ctx.is_at_bot:
        logit += _get_adjusted_weight("at_bot")
    if ctx.reply_to_bot:
        logit += _get_adjusted_weight("reply_to_bot")
    if _bot_nickname_in(ctx.text):
        logit += _get_adjusted_weight("nickname")

    if ctx.is_pure_media:
        logit += _get_adjusted_weight("media_penalty")

    if ctx.intent == "question":
        logit += _get_adjusted_weight("question") * ctx.intent_confidence
    elif ctx.intent == "command":
        logit += _get_adjusted_weight("command") * ctx.intent_confidence
    elif ctx.intent == "emotion":
        logit += _get_adjusted_weight("emotion") * ctx.intent_confidence

    spam_score = _compute_spam_score(ctx.text)
    if spam_score > sd["trigger_score"]:
        logit += _get_adjusted_weight("spam_penalty") * spam_score

    logit += _get_adjusted_weight("topic_relevance") * _topic_relevance(ctx.text, ctx.group_id)
    logit += _get_adjusted_weight("continuation") * _is_continuation(ctx.text, ctx.last_bot_msg)

    lb = reply_cfg.length_bonuses
    n = len(ctx.text)
    if n < lb["too_short_max"]:
        logit += lb["too_short_score"]
    elif n <= lb["good_short_max"]:
        logit += lb["good_short_score"]
    elif n > lb["too_long_min"]:
        logit += lb["too_long_score"]

    if ctx.last_msg_from_bot:
        logit += _get_adjusted_weight("last_from_bot")
    logit += _get_adjusted_weight("active_human") if ctx.active_human_conversation else 0
    logit += _group_dynamic_score(ctx)

    logit += profile.score()
    logit += cooldown.get_cooldown_penalty(ctx.group_id)
    if ctx.user_rate_limited:
        logit += lb["rate_limit_score"]

    return 1.0 / (1.0 + math.exp(-logit))


def _get_threshold(ctx: MessageContext) -> float:
    th = reply_cfg.thresholds
    if ctx.is_private:
        base = th["private"]
    elif user_profile_manager.get(ctx.user_id).is_vip:
        base = th["admin"]
    else:
        mpm = ctx.group_msg_per_minute
        if mpm > reply_cfg.busy_threshold_mpm:
            base = th["busy"]
        elif mpm < reply_cfg.quiet_threshold_mpm:
            base = th["quiet"]
        else:
            base = th["group"]

    ta = reply_cfg.threshold_adjustments
    ar = ctx.group_approval_rate
    if ar > 0.7:
        base += ta["approval_high_delta"]
    elif ar < 0.3:
        base += ta["approval_low_delta"]

    if ctx.intent == "question":
        base += ta["question_delta"]
    elif ctx.intent == "spam":
        base += ta["spam_delta"]
    elif ctx.intent == "programming":
        base += ta["programming_delta"]

    return max(th.get("min", 0.15), min(th.get("max", 0.85), base))


def should_reply(ctx: MessageContext, cooldown: CooldownManager = cooldown_manager) -> bool:
    prob = compute_reply_prob(ctx, cooldown)
    threshold = _get_threshold(ctx)
    if prob >= threshold:
        cooldown.record_reply(ctx.group_id)
        return True
    return False


def build_context(
    text: str,
    user_id: str,
    group_id: str = "",
    is_at_bot: bool = False,
    reply_to_bot: bool = False,
    is_pure_media: bool = False,
    **kwargs
) -> MessageContext:
    ctx = MessageContext(
        text=text,
        user_id=user_id,
        group_id=group_id,
        is_at_bot=is_at_bot,
        reply_to_bot=reply_to_bot,
        is_pure_media=is_pure_media,
        **kwargs
    )
    if group_id:
        state = group_state_manager.get(group_id)
        ctx.group_msg_per_minute = state.msg_per_minute
        ctx.group_msg_per_5min = state.msg_per_5min
        ctx.group_approval_rate = state.approval_rate
        ctx.active_human_conversation = state.is_active_conversation
        ctx.last_msg_from_bot = state.last_msg_from_bot
        ctx.last_bot_msg = state.last_bot_msg
    return ctx
