"""
消息处理服务
学习和回复是两套独立的流程
"""
import time
import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

from joha.ai.generator import generator
from joha.core.message_builder import message_builder
from joha.core.tool_registry import get_tool_registry, tool_registry
from joha.managers.history_manager import history_manager
from joha.managers.style_learner import style_learner
from joha.managers.group_conversation import group_conversation
from joha.managers.group_memory import group_memory_manager
from joha.config.logger import johalog_logger, ai_logger, tprint
from joha.config.config_manager import config
from joha.config.group_mode_config import group_mode_config
from joha.decision import get_decision_engine


@dataclass
class MessageContext:
    """消息处理上下文，贯穿整个处理流程"""
    # 原始输入
    user_id: str = ""
    group_id: Optional[str] = None
    message: str = ""
    images: list = field(default_factory=list)
    is_at_bot: bool = False
    reply_to_bot: bool = False
    is_pure_sticker_or_image: bool = False
    force_reply: bool = False

    # 消息队列合并结果
    messages: list = field(default_factory=list)
    merged_text: str = ""
    is_merged: bool = False

    # 决策结果
    should_reply: bool = False

    # LLM 上下文
    context_messages: list = field(default_factory=list)
    response: Optional[str] = None

    # 元数据
    received_at: float = field(default_factory=time.time)


class MessageService:

    def __init__(self):
        self.mode = config.get('bot.mode', 'passive')
        self.started_at = time.time()
        self.total_messages = 0
        self.learned_messages = 0
        self.reply_decisions = 0
        self.skipped_replies = 0
        self.generated_replies = 0
        self.failed_replies = 0

        # 初始化 Tool Registry（自动发现工具）
        self.tool_registry = tool_registry
        discovered = self.tool_registry.auto_discover()
        message_builder.tool_registry = self.tool_registry
        johalog_logger.info(f"ToolRegistry 已加载 {discovered} 个工具")

        # 初始化决策引擎
        self.decision_engine = get_decision_engine()

        self.group_modes: Dict[str, str] = group_mode_config.get_all_modes()
        johalog_logger.info(f"已初始化 {len(self.group_modes)} 个群组模式")

    async def process_message(
        self,
        userid: str,
        message: str,
        group_id: Optional[str] = None,
        force_reply: bool = False,
        is_at_bot: bool = False,
        reply_to_bot: bool = False,
        is_pure_sticker_or_image: bool = False,
        images: list = None,
        merged_text: str = "",
        merged_messages: list = None,
        is_merged: bool = False,
    ) -> Optional[str]:
        userid_str = str(userid)
        message = message.strip()
        images = images or []
        merged_messages = merged_messages or []

        if not message and not images:
            return None

        self.total_messages += 1
        log_msg = message if message else f"[图片 x{len(images)}]"
        ai_logger.info("收到消息", extra={"userid": userid_str, "msg_content": log_msg})
        tprint("info", f"[消息] 群{group_id} | 用户{userid_str}: {log_msg}")

        ctx = MessageContext(
            user_id=userid_str,
            group_id=str(group_id) if group_id else None,
            message=message,
            images=images,
            is_at_bot=is_at_bot,
            reply_to_bot=reply_to_bot,
            is_pure_sticker_or_image=is_pure_sticker_or_image,
            force_reply=force_reply,
            messages=merged_messages,
            merged_text=merged_text or message,
            is_merged=is_merged,
        )

        # 阶段1：学习
        self._learn_stage(ctx)

        # 阶段2：群模式 + 决策
        if not await self._decide_stage(ctx):
            return ctx.response

        # 阶段3：视觉（图片转文字）
        if not await self._vision_stage(ctx):
            return None

        # 阶段4：上下文构建
        if not await self._context_stage(ctx):
            return None

        # 阶段5：生成回复
        if not await self._generate_stage(ctx):
            return None

        # 阶段6：记录存储
        self._record_stage(ctx)

        return ctx.response

    # ==================== 学习阶段 ====================

    def _learn_stage(self, ctx: MessageContext) -> None:
        try:
            learn_msg = ctx.message if ctx.message else f"[用户发送了一张图片]"
            history_manager.add_message(ctx.user_id, learn_msg, group_id=ctx.group_id)
            style_learner.learn_from_message(ctx.user_id, learn_msg)
            self.learned_messages += 1
            johalog_logger.debug(f"[学习] 记录用户 {ctx.user_id} 的消息并学习风格")
        except Exception as e:
            johalog_logger.error(f"学习失败：{e}")

    # ==================== 决策阶段 ====================

    async def _decide_stage(self, ctx: MessageContext) -> bool:
        group_mode = self.get_group_mode(ctx.group_id) if ctx.group_id else self.get_global_mode()

        if group_mode != "active" and not ctx.force_reply:
            self.skipped_replies += 1
            return False

        result = self.decision_engine.process(
            text=ctx.merged_text or ctx.message,
            user_id=ctx.user_id,
            group_id=ctx.group_id or "",
            is_at_bot=ctx.is_at_bot,
            reply_to_bot=ctx.reply_to_bot,
            is_pure_media=ctx.is_pure_sticker_or_image,
            group_mode=group_mode,
            force_reply=ctx.force_reply,
        )
        self.reply_decisions += 1

        ctx.should_reply = result.should_reply

        if not result.should_reply:
            self.skipped_replies += 1
            return False

        return True

    # ==================== 视觉阶段 ====================

    async def _vision_stage(self, ctx: MessageContext) -> bool:
        if not ctx.images:
            return True

        current_model = generator.current_model
        if not self._supports_multimodal(current_model):
            tprint("warning", f"[多模态] 模型 {current_model} 不支持图片，已跳过 {len(ctx.images)} 张图片")
            johalog_logger.warning(f"模型 {current_model} 不支持多模态，已跳过图片")
            ctx.images = []

        return True

    @staticmethod
    def _supports_multimodal(model_name: str) -> bool:
        MULTIMODAL_MODEL_PREFIXES = (
            "gpt-4o", "gpt-4-vision", "gpt-4-turbo",
            "claude-3", "claude-3.5", "claude-3.7",
            "gemini-1.5", "gemini-2", "gemini-2.5",
            "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
            "qwen-omni", "qwen3.5-omni",
            "glm-4v", "cogvlm", "cogagent",
            "yi-vision", "yi-vl",
            "internvl", "internlm-xcomposer",
            "llava", "bakllava", "llama3.2-vision", "llama-3.2-vision",
            "pixtral", "deepseek-vl",
        )
        model_lower = model_name.lower()
        return any(model_lower.startswith(prefix.lower()) for prefix in MULTIMODAL_MODEL_PREFIXES)

    # ==================== 上下文阶段 ====================

    async def _context_stage(self, ctx: MessageContext) -> bool:
        try:
            history = history_manager.load_history(ctx.user_id, group_id=ctx.group_id)
            ctx.context_messages = message_builder.build(
                user_id=ctx.user_id,
                message=ctx.merged_text or ctx.message,
                images=ctx.images,
                persona_name="joha",
                history=history,
                include_style=True,
                group_id=ctx.group_id,
            )
        except Exception as e:
            johalog_logger.error(f"构建上下文失败：{e}")
            return False
        return True

    # ==================== 生成阶段 ====================

    async def _generate_stage(self, ctx: MessageContext) -> bool:
        log_msg = (ctx.merged_text or ctx.message)[:30]
        tprint("info", f"[AI] 请求中... | 用户{ctx.user_id} | 消息: {log_msg}{'...' if len(ctx.merged_text or ctx.message) > 30 else ''}")

        try:
            response = await generator.chat(
                messages=ctx.context_messages,
                temperature=0.7,
                max_tokens=1024,
            )
        except Exception as bot_err:
            tprint("warning", f"[Generator] 生成失败: {bot_err}")
            response = None

        if not response:
            self.failed_replies += 1
            tprint("warning", f"[AI] 未生成回复，已跳过发送到群 | 用户{ctx.user_id} | 消息: {log_msg}")
            johalog_logger.warning(
                f"[回复生成失败] 用户:{ctx.user_id}, 消息:{log_msg[:20]}..., 已跳过群发送"
            )
            return False

        tprint("info", f"[AI] 回复: {response}")
        johalog_logger.info(
            f"[回复生成] 用户:{ctx.user_id}, 消息:{log_msg[:20]}..., 回复:{response[:20]}..."
        )

        ctx.response = response
        self.generated_replies += 1
        return True

    # ==================== 记录阶段 ====================

    def _record_stage(self, ctx: MessageContext) -> None:
        if ctx.group_id:
            try:
                group_conversation.add_user_message(
                    str(ctx.group_id), ctx.user_id, ctx.merged_text or ctx.message
                )
                group_conversation.add_bot_reply(str(ctx.group_id), ctx.response)
            except Exception as e:
                johalog_logger.error(f"群对话记录写入失败：{e}")

    # ==================== 模式管理 ====================

    def get_global_mode(self) -> str:
        config.load()
        mode = config.get('bot.mode', self.mode)
        if mode not in ["active", "passive"]:
            return self.mode
        self.mode = mode
        return mode

    def set_global_mode(self, mode: str) -> None:
        if mode not in ["active", "passive"]:
            raise ValueError(f"无效的模式: {mode}")
        config.load()
        config.set('bot.mode', mode)
        config.save()
        self.mode = mode

    def get_group_mode(self, group_id: str) -> str:
        return group_mode_config.get_mode(group_id, self.get_global_mode())

    def set_group_mode(self, group_id: str, mode: str) -> None:
        if mode not in ["active", "passive"]:
            raise ValueError(f"无效的模式: {mode}")
        group_mode_config.set_mode(group_id, mode)
        self.group_modes = group_mode_config.get_all_modes()

    # ==================== 统计 ====================

    def get_stats(self) -> Dict[str, Any]:
        uptime = int(time.time() - self.started_at)
        from joha.decision.group_state import group_state_manager
        gs = group_state_manager.get_stats()
        return {
            "uptime": uptime,
            "total_messages": self.total_messages,
            "learned_messages": self.learned_messages,
            "reply_decisions": self.reply_decisions,
            "skipped_replies": self.skipped_replies,
            "generated_replies": self.generated_replies,
            "failed_replies": self.failed_replies,
            "active_groups": len([g for g, m in self.group_modes.items() if m == "active"]),
            "passive_groups": len([g for g, m in self.group_modes.items() if m == "passive"]),
            "tracked_groups": gs["total_groups"],
            "group_total_messages": gs["total_messages"],
            "group_bot_replies": gs["total_bot_replies"],
            "avg_msg_per_min": round(gs["avg_msg_per_min"], 1),
        }

    def get_stats_str(self) -> str:
        stats = self.get_stats()
        return (
            f"=== Joha 服务统计 ===\n"
            f"运行时间: {stats['uptime']} 秒\n"
            f"收到消息: {stats['total_messages']}\n"
            f"学习消息: {stats['learned_messages']}\n"
            f"回复决策: {stats['reply_decisions']}\n"
            f"生成回复: {stats['generated_replies']}\n"
            f"跳过回复: {stats['skipped_replies']}\n"
            f"失败回复: {stats['failed_replies']}\n"
            f"活跃群组: {stats['active_groups']}\n"
            f"被动群组: {stats['passive_groups']}\n"
            f"追踪群组: {stats['tracked_groups']}\n"
            f"群总消息: {stats['group_total_messages']}\n"
            f"=================="
        )


# 全局服务实例
message_service = MessageService()
