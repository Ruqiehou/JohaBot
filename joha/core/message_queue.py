"""
消息队列管理器 - 智能消息合并
将短时间内的多条消息合并为一条进行处理，减少废话和冗余回复
"""
import time
import asyncio
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from joha.config.logger import tprint, johalog_logger


@dataclass
class QueuedMessage:
    """队列中的消息"""
    user_id: str
    group_id: str
    message: str
    timestamp: float
    images: List[str] = field(default_factory=list)
    is_at_bot: bool = False
    reply_to_bot: bool = False
    is_pure_sticker_or_image: bool = False


@dataclass
class MergedMessage:
    """合并后的消息"""
    user_id: str
    group_id: str
    messages: List[str]  # 原始消息列表
    merged_text: str     # 合并后的文本
    timestamp: float     # 第一条消息的时间
    last_timestamp: float  # 最后一条消息的时间
    images: List[str] = field(default_factory=list)
    is_at_bot: bool = False
    reply_to_bot: bool = False
    is_pure_sticker_or_image: bool = False
    
    @property
    def duration(self) -> float:
        """消息持续时间（秒）"""
        return self.last_timestamp - self.timestamp
    
    @property
    def count(self) -> int:
        """消息数量"""
        return len(self.messages)


class MessageQueueManager:
    """消息队列管理器 - 智能合并短时间内的消息"""
    
    def __init__(self, merge_window: float = None):
        """
        初始化消息队列管理器
        
        Args:
            merge_window: 合并窗口时间（秒），显式传入时覆盖配置文件
        """
        # 默认值（配置文件缺失时使用）
        self.merge_window = 120.0
        self.max_queue_size = 5
        self.min_messages_to_merge = 2
        self.enabled = True

        # 从配置文件读取设置
        try:
            from joha.config.config_manager import config
            queue_config = config.get('message_queue', {}) or {}
            self.merge_window = queue_config.get('merge_window', self.merge_window)
            self.max_queue_size = queue_config.get('max_queue_size', self.max_queue_size)
            self.min_messages_to_merge = queue_config.get('min_messages_to_merge', self.min_messages_to_merge)
            self.enabled = queue_config.get('enabled', self.enabled)
        except Exception:
            pass

        # 显式传入的 merge_window 优先于配置文件
        if merge_window is not None:
            self.merge_window = merge_window
        
        # 群组的消息队列: {group_id: [QueuedMessage]}
        self.message_queues: Dict[str, List[QueuedMessage]] = defaultdict(list)
        
        tprint("info", f"[消息队列] 已初始化，合并窗口: {self.merge_window}秒, 最大队列大小: {self.max_queue_size}, 最小合并数: {self.min_messages_to_merge}")
    
    def _get_queue_key(self, user_id: str, group_id: str) -> str:
        """获取队列键（仅使用群组ID）"""
        return str(group_id)
    
    async def add_message(
        self,
        user_id: str,
        group_id: str,
        message: str,
        images: List[str] = None,
        is_at_bot: bool = False,
        reply_to_bot: bool = False,
        is_pure_sticker_or_image: bool = False,
    ) -> Optional[MergedMessage]:
        """
        添加消息到队列，如果达到合并条件则返回合并后的消息
        
        Args:
            user_id: 用户ID
            group_id: 群组ID
            message: 消息内容
            images: 图片列表
            is_at_bot: 是否@机器人
            reply_to_bot: 是否回复机器人
            is_pure_sticker_or_image: 是否为纯表情/图片
            
        Returns:
            如果需要立即处理则返回合并后的消息，否则返回None
        """
        # 如果消息队列功能被禁用，直接返回合并消息（不等待）
        if not self.enabled:
            return MergedMessage(
                user_id=str(user_id),
                group_id=str(group_id),
                messages=[message.strip()] if message.strip() else [],
                merged_text=message.strip(),
                timestamp=time.time(),
                last_timestamp=time.time(),
                images=images or [],
                is_at_bot=is_at_bot,
                reply_to_bot=reply_to_bot,
                is_pure_sticker_or_image=is_pure_sticker_or_image,
            )
        
        images = images or []
        key = self._get_queue_key(user_id, group_id)
        current_time = time.time()
        
        # 创建队列消息
        queued_msg = QueuedMessage(
            user_id=str(user_id),
            group_id=str(group_id),
            message=message.strip(),
            timestamp=current_time,
            images=images,
            is_at_bot=is_at_bot,
            reply_to_bot=reply_to_bot,
            is_pure_sticker_or_image=is_pure_sticker_or_image,
        )
        
        # 添加到队列
        self.message_queues[key].append(queued_msg)
        
        # 检查是否需要立即处理
        should_process = await self._should_process_now(key, current_time)
        
        if should_process:
            return await self._process_queue(key)
        
        return None
    
    async def _should_process_now(self, key: str, current_time: float) -> bool:
        """
        判断是否应该立即处理队列
        
        触发条件：
        1. 队列中有@机器人的消息（立即处理）
        2. 队列中有回复机器人的消息（立即处理）
        3. 队列中消息数量过多（超过max_queue_size，立即处理）
        4. 距离第一条消息已超过合并窗口 AND 消息数量>=min_messages_to_merge（超时处理）
        
        注意：如果只有1条消息且未超时，不会触发处理，继续等待
        """
        queue = self.message_queues.get(key, [])
        
        if not queue:
            return False
        
        first_msg_time = queue[0].timestamp
        msg_count = len(queue)
        
        # 条件1: 有@机器人的消息（优先级最高，立即处理）
        if any(msg.is_at_bot for msg in queue):
            return True
        
        # 条件2: 有回复机器人的消息（优先级高，立即处理）
        if any(msg.reply_to_bot for msg in queue):
            return True
        
        # 条件3: 消息数量过多（立即处理）
        if msg_count >= self.max_queue_size:
            return True
        
        # 条件4: 超过合并窗口 AND (消息数量达到最小合并数 OR 仅一条消息但已超时)
        # 避免单条消息永久搁置在队列中
        time_elapsed = current_time - first_msg_time
        if time_elapsed >= self.merge_window and (msg_count >= self.min_messages_to_merge or msg_count == 1):
            return True
        
        return False
    
    async def _process_queue(self, key: str) -> Optional[MergedMessage]:
        """
        处理队列，合并消息
        
        Args:
            key: 队列键（群组ID）
            
        Returns:
            合并后的消息，如果队列为空则返回None
        """
        queue = self.message_queues.get(key, [])
        
        if not queue:
            return None
        
        # 提取所有消息
        messages = []
        all_images = []
        is_at_bot = False
        reply_to_bot = False
        is_pure_sticker = False
        
        for msg in queue:
            if msg.message:
                messages.append(msg.message)
            all_images.extend(msg.images)
            if msg.is_at_bot:
                is_at_bot = True
            if msg.reply_to_bot:
                reply_to_bot = True
            if msg.is_pure_sticker_or_image:
                is_pure_sticker = True
        
        # 如果没有文本消息但有图片，添加占位符
        if not messages and all_images:
            messages.append("[图片]")
        
        # 合并文本
        merged_text = self._merge_messages(messages)
        
        # 创建合并消息（使用第一个消息的用户ID作为代表）
        merged = MergedMessage(
            user_id=queue[0].user_id,
            group_id=queue[0].group_id,
            messages=messages,
            merged_text=merged_text,
            timestamp=queue[0].timestamp,
            last_timestamp=queue[-1].timestamp,
            images=all_images,
            is_at_bot=is_at_bot,
            reply_to_bot=reply_to_bot,
            is_pure_sticker_or_image=is_pure_sticker,
        )
        
        # 清空队列
        self.message_queues[key] = []
        
        tprint("info", 
            f"[消息合并] 群{merged.group_id} | "
            f"合并{merged.count}条消息 ({merged.duration:.1f}秒) | "
            f"内容: {merged_text[:50]}{'...' if len(merged_text) > 50 else ''}"
        )
        
        johalog_logger.info(
            f"[消息合并] 群:{merged.group_id}, "
            f"合并数:{merged.count}, 时长:{merged.duration:.1f}s"
        )
        
        return merged
    
    def _merge_messages(self, messages: List[str]) -> str:
        """
        智能合并多条消息
        
        策略：
        1. 如果只有一条消息，直接返回
        2. 如果是短句快速连续发送，用空格连接
        3. 如果有完整的句子，用换行分隔
        4. 去除重复内容
        """
        if not messages:
            return ""
        
        if len(messages) == 1:
            return messages[0]
        
        # 去重（保持顺序）
        seen = set()
        unique_messages = []
        for msg in messages:
            if msg not in seen:
                seen.add(msg)
                unique_messages.append(msg)
        
        messages = unique_messages
        
        # 判断是否为短句（平均长度小于15字符）
        avg_length = sum(len(msg) for msg in messages) / len(messages)
        
        if avg_length < 15:
            # 短句用空格连接
            return " ".join(messages)
        else:
            # 长句用换行分隔
            return "\n".join(messages)
    
    async def force_process(self, user_id: str, group_id: str) -> Optional[MergedMessage]:
        """
        强制处理指定群组的队列
        
        Args:
            user_id: 用户ID（保留参数以兼容接口）
            group_id: 群组ID
            
        Returns:
            合并后的消息，如果队列为空则返回None
        """
        key = self._get_queue_key(user_id, group_id)
        return await self._process_queue(key)
    
    async def process_expired(self) -> List[MergedMessage]:
        """
        处理所有过期的队列
        
        Returns:
            需要处理的合并消息列表
        """
        current_time = time.time()
        expired_keys = []
        
        # 找出所有过期的队列
        for key, queue in self.message_queues.items():
            if queue and (current_time - queue[0].timestamp >= self.merge_window):
                expired_keys.append(key)
        
        # 处理过期队列
        results = []
        for key in expired_keys:
            merged = await self._process_queue(key)
            if merged:
                results.append(merged)
        
        return results
    
    def get_queue_status(self) -> Dict:
        """获取队列状态统计"""
        total_queues = len(self.message_queues)
        active_queues = sum(1 for q in self.message_queues.values() if q)
        total_messages = sum(len(q) for q in self.message_queues.values())
        
        return {
            "total_queues": total_queues,
            "active_queues": active_queues,
            "total_queued_messages": total_messages,
            "merge_window": self.merge_window,
        }
    
    def clear_queue(self, user_id: str, group_id: str) -> int:
        """
        清空指定群组的队列
        
        Args:
            user_id: 用户ID（保留参数以兼容接口）
            group_id: 群组ID
            
        Returns:
            清空的消息数量
        """
        key = self._get_queue_key(user_id, group_id)
        count = len(self.message_queues.get(key, []))
        self.message_queues[key] = []
        return count
    
    def clear_all(self) -> int:
        """清空所有队列"""
        total = sum(len(q) for q in self.message_queues.values())
        self.message_queues.clear()
        return total


# 全局消息队列管理器实例（参数从 config.json 的 message_queue 段读取）
message_queue_manager = MessageQueueManager()
