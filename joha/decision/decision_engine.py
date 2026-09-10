"""
决策引擎（总）v1.0
统一编排决策流水线：意图分析 → 回复概率 → 回复决策
各子模块（分）保持专注单一职责，由本引擎串联整合
"""
from typing import Dict, Optional
from dataclasses import dataclass

from joha.decision.reply_decision import (
    should_reply, build_context, MessageContext, compute_reply_prob,
)
from joha.decision.cooldown import cooldown_manager, CooldownManager
from joha.config.logger import johalog_logger, tprint


@dataclass
class EngineResult:
    """决策引擎输出结果"""
    should_reply: bool = False
    probability: float = 0.0
    intent: str = "chat"
    intent_confidence: float = 0.0


class DecisionEngine:
    """决策引擎 — 总分架构的"总"

    对外仅暴露 process() 入口，内部编排完整的决策流水线。
    """

    def __init__(
        self,
        cooldown: CooldownManager = cooldown_manager,
    ):
        self.cooldown = cooldown
        self._stats = {"total_calls": 0, "replied": 0, "skipped": 0}

    def process(
        self,
        text: str,
        user_id: str,
        group_id: str = "",
        is_at_bot: bool = False,
        reply_to_bot: bool = False,
        is_pure_media: bool = False,
        is_private: bool = False,
        group_mode: str = "passive",
        force_reply: bool = False,
        **kwargs,
    ) -> EngineResult:
        """
        完整决策流水线

        Args:
            text: 消息内容
            user_id: 用户 ID
            group_id: 群 ID
            is_at_bot: 是否 @机器人
            reply_to_bot: 是否回复机器人
            is_pure_media: 纯媒体消息
            is_private: 私聊
            group_mode: 群模式（active / passive）
            force_reply: 强制回复

        Returns:
            EngineResult 包含全部决策信息
        """
        self._stats["total_calls"] += 1
        result = EngineResult()

        if group_mode != "active" and not force_reply:
            self._stats["skipped"] += 1
            return result

        ctx = build_context(
            text=text,
            user_id=user_id,
            group_id=group_id,
            is_at_bot=is_at_bot,
            reply_to_bot=reply_to_bot,
            is_pure_media=is_pure_media,
        )

        prob = compute_reply_prob(ctx, self.cooldown)
        result.probability = prob
        result.intent = ctx.intent
        result.intent_confidence = ctx.intent_confidence

        decision = should_reply(ctx, self.cooldown)
        result.should_reply = decision

        self._log_decision(result, ctx)
        if result.should_reply:
            self._stats["replied"] += 1
        else:
            self._stats["skipped"] += 1

        return result

    def _log_decision(self, result: EngineResult, ctx: MessageContext):
        """记录决策日志"""
        tprint("info",
            f"[决策] 概率={result.probability:.3f} | "
            f"意图={result.intent}({result.intent_confidence:.2f}) | "
            f"{'✅ 回复' if result.should_reply else '❌ 跳过'}"
        )
        johalog_logger.debug(
            f"[回复决策] 概率={result.probability:.3f}, 意图={result.intent}, "
            f"决策={'✅回复' if result.should_reply else '❌不回复'}"
        )

    def get_stats(self) -> Dict:
        """获取引擎统计"""
        return {
            "total_calls": self._stats["total_calls"],
            "replied": self._stats["replied"],
            "skipped": self._stats["skipped"],
            "reply_rate": round(
                self._stats["replied"] / max(self._stats["total_calls"], 1), 3
            ),
        }

    def refresh(self):
        """刷新各子模块状态"""
        self.cooldown = cooldown_manager


_engine_instance: Optional[DecisionEngine] = None


def get_decision_engine() -> DecisionEngine:
    """获取决策引擎（单例）"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = DecisionEngine()
    return _engine_instance
