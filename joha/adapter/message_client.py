"""
MessageClient 类
精简版：仅保留装饰器模式的事件注册与生命周期管理
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeAlias

from joha.adapter.config import setup_logging
from joha.adapter.transport import NapCatClient
from joha.adapter.protocol import BotAPI
from joha.adapter.protocol.events import (
    GroupMessageEvent,
    NoticeEvent,
    PrivateMessageEvent,
    RequestEvent,
)

# 设置日志系统
setup_logging()

logger: logging.Logger = logging.getLogger(__name__)

# ---- 类型别名 ----
GroupMsgHandler: TypeAlias = Callable[[GroupMessageEvent], Awaitable[None]]
PrivateMsgHandler: TypeAlias = Callable[[PrivateMessageEvent], Awaitable[None]]
NoticeHandler: TypeAlias = Callable[[NoticeEvent], Awaitable[None]]
RequestHandler: TypeAlias = Callable[[RequestEvent], Awaitable[None]]


class MessageClient:
    """消息客户端 —— 专注于事件封装和装饰器模式
    
    职责：
    - 提供装饰器 API 注册事件处理器
    - 事件路由和分发
    - 生命周期管理（启动/停止）
    
    底层连接由 NapCatClient 负责
    """

    def __init__(
        self,
        ws_url: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> None:
        self.client: NapCatClient = NapCatClient(ws_url=ws_url, access_token=access_token)
        self.api: BotAPI = BotAPI(self.client)

        self._group_message_handlers: List[GroupMsgHandler] = []
        self._private_message_handlers: List[PrivateMsgHandler] = []
        self._notice_handlers: List[NoticeHandler] = []
        self._request_handlers: List[RequestHandler] = []

        # 主循环 tick 回调（用于后台周期任务，如清理过期消息队列）
        self._tick_handler: Optional[Callable[[], Awaitable[None]]] = None

        # 设置消息回调
        self.client.set_message_callback(self._handle_message)

    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """处理消息 —— 事件路由"""
        post_type = data.get("post_type")
        message_type = data.get("message_type")

        # 消息事件（群/私聊）
        if post_type == "message" or (post_type is None and message_type in ("group", "private")):
            if message_type == "group":
                try:
                    event = GroupMessageEvent.from_dict(data)
                    await self._run_handlers(
                        [self._print_group_message, *self._group_message_handlers],
                        event,
                        "处理群消息事件时出错",
                    )
                except Exception as e:
                    logger.error(f"处理群消息时出错: {e}", exc_info=True)
            elif message_type == "private":
                try:
                    event = PrivateMessageEvent.from_dict(data)
                    await self._run_handlers(
                        [self._print_private_message, *self._private_message_handlers],
                        event,
                        "处理私聊消息事件时出错",
                    )
                except Exception as e:
                    logger.error(f"处理私聊消息时出错: {e}", exc_info=True)

        # 通知事件
        elif post_type == "notice":
            try:
                event = NoticeEvent.from_dict(data)
                await self._run_handlers(self._notice_handlers, event, "处理通知事件时出错")
            except Exception as e:
                logger.error(f"处理通知时出错: {e}", exc_info=True)

        # 请求事件
        elif post_type == "request":
            try:
                event = RequestEvent.from_dict(data)
                await self._run_handlers(self._request_handlers, event, "处理请求事件时出错")
            except Exception as e:
                logger.error(f"处理请求时出错: {e}", exc_info=True)

    async def _run_handlers(
        self,
        handlers: List[Callable[[Any], Awaitable[None]]],
        event: Any,
        error_label: str,
    ) -> None:
        tasks = [asyncio.create_task(handler(event)) for handler in handlers]
        for task in asyncio.as_completed(tasks):
            try:
                await task
            except Exception as e:
                logger.error(f"{error_label}: {e}", exc_info=True)

    async def _print_group_message(self, event: GroupMessageEvent) -> None:
        """异步打印群消息"""
        print(
            f"[群消息] 群:{event.group_id} "
            f"用户:{event.user_name}({event.user_id}): "
            f"{event.message.plain_text}"
        )

    async def _print_private_message(self, event: PrivateMessageEvent) -> None:
        """异步打印私聊消息"""
        print(
            f"[私聊] 用户:{event.user_name}({event.user_id}): "
            f"{event.message.plain_text}"
        )

    # ======================== 装饰器式事件注册 ========================

    def set_tick_handler(self, handler: Callable[[], Awaitable[None]]) -> None:
        """设置主循环 tick 回调（每秒执行一次，用于后台周期任务）

        Args:
            handler: 异步无参回调
        """
        self._tick_handler = handler

    def on_group_message(self) -> Callable[[GroupMsgHandler], GroupMsgHandler]:
        """装饰器：注册群消息事件处理器

        Returns:
            装饰器函数
        """
        def decorator(func: GroupMsgHandler) -> GroupMsgHandler:
            if func not in self._group_message_handlers:
                self._group_message_handlers.append(func)
            return func
        return decorator

    def on_private_message(self) -> Callable[[PrivateMsgHandler], PrivateMsgHandler]:
        """装饰器：注册私聊消息事件处理器

        Returns:
            装饰器函数
        """
        def decorator(func: PrivateMsgHandler) -> PrivateMsgHandler:
            if func not in self._private_message_handlers:
                self._private_message_handlers.append(func)
            return func
        return decorator

    def on_notice(self) -> Callable[[NoticeHandler], NoticeHandler]:
        """装饰器：注册通知事件处理器

        Returns:
            装饰器函数
        """
        def decorator(func: NoticeHandler) -> NoticeHandler:
            if func not in self._notice_handlers:
                self._notice_handlers.append(func)
            return func
        return decorator

    def on_request(self) -> Callable[[RequestHandler], RequestHandler]:
        """装饰器：注册请求事件处理器

        Returns:
            装饰器函数
        """
        def decorator(func: RequestHandler) -> RequestHandler:
            if func not in self._request_handlers:
                self._request_handlers.append(func)
            return func
        return decorator

    # ======================== 生命周期 ========================

    async def run_frontend(
        self, debug: bool = False
    ) -> None:
        """运行消息客户端（事件循环入口）

        Args:
            debug: 是否开启调试模式
        """
        try:
            await self.client.connect()

            if debug:
                logger.setLevel(logging.DEBUG)
                logger.info("调试模式已开启")

            logger.info("=" * 50)
            logger.info("消息客户端已启动")
            logger.info("=" * 50)
            logger.info(
                f"注册的群消息处理器数量: {len(self._group_message_handlers)}"
            )
            logger.info(
                f"注册的私聊消息处理器数量: {len(self._private_message_handlers)}"
            )
            logger.info(f"注册的通知处理器数量: {len(self._notice_handlers)}")
            logger.info(f"注册的请求处理器数量: {len(self._request_handlers)}")
            logger.info("=" * 50)
            logger.info("等待消息中...")
            logger.info("=" * 50)

            while self.client.connected:
                await asyncio.sleep(1)
                if self._tick_handler:
                    try:
                        await self._tick_handler()
                    except Exception as e:
                        logger.error(f"tick 回调执行失败: {e}", exc_info=True)

        except KeyboardInterrupt:
            logger.info("收到中断信号，正在关闭...")
        except Exception as e:
            logger.error(f"发生错误: {e}", exc_info=True)
        finally:
            await self.client.disconnect()
            logger.info("消息客户端已关闭")

    @classmethod
    def run(cls) -> None:
        """一键启动：从 connection.yaml 读取配置并运行消息客户端"""
        from joha.adapter.config import config_manager

        client = cls(
            ws_url=config_manager.get("napcat.ws_url", "ws://127.0.0.1:3002"),
            access_token=config_manager.get("napcat.access_token", ""),
        )
        client.start(
            debug=config_manager.get("settings.debug", False),
        )

    def __call__(self, debug: bool = False) -> None:
        """直接调用实例启动消息客户端"""
        self.start(debug=debug)

    def start(self, debug: bool = False) -> None:
        """启动消息客户端（便捷方法，兼容已有事件循环）"""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self.run_frontend(debug=debug))
                return
        except RuntimeError:
            pass
        asyncio.run(self.run_frontend(debug=debug))
