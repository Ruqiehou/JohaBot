"""
Joha 统一启动入口
"""

from joha.adapter import MessageClient, GroupMessageEvent, config_manager
from joha.core import message_handler, runtime_context
from joha.core.message_handler import process_merged_message
from joha.core.message_queue import message_queue_manager
from joha.managers.user_profile import user_profile_manager
from joha.managers.style_learner import style_learner
from joha.decision.cooldown import cooldown_manager
from joha.core.hot_reload import hot_reloader
from joha.config.logger import tprint


RUN_CONNECT_CONFIG = {
    # 这里可以临时覆盖 adapter/connection.yaml；None 表示使用 YAML 配置
    "ws_url": None,
    "access_token": None,
    "bot_uin": None,
    "debug": None,
}

WS_URL = RUN_CONNECT_CONFIG["ws_url"] or config_manager.get("napcat.ws_url", "ws://127.0.0.1:3002")
ACCESS_TOKEN = RUN_CONNECT_CONFIG["access_token"] or config_manager.get("napcat.access_token", "")
BOT_UIN = RUN_CONNECT_CONFIG["bot_uin"] or config_manager.get("napcat.bot_uin", "8888888888")
DEBUG = RUN_CONNECT_CONFIG["debug"] if RUN_CONNECT_CONFIG["debug"] is not None else config_manager.get("settings.debug", True)

runtime_context.bot_uin = int(BOT_UIN)

client = MessageClient(
    ws_url=WS_URL,
    access_token=ACCESS_TOKEN,
)


@client.on_group_message()
async def joha_agent(event: GroupMessageEvent):
    await message_handler.process_group_message(event, client.api)


async def _tick_handle_expired() -> None:
    """主循环 tick：处理超时队列消息并定期落盘用户画像"""
    try:
        expired = await message_queue_manager.process_expired()
        for merged in expired:
            tprint("info", f"[过期队列] 处理超时消息 群{merged.group_id} | {merged.merged_text[:30]}")
            await process_merged_message(merged, client.api)
    except Exception as e:
        tprint("warning", f"[过期队列] 处理失败: {e}")

    try:
        user_profile_manager.save_all()
        style_learner.save_all()
        cooldown_manager.save()
    except Exception as e:
        tprint("warning", f"[用户数据] 保存失败: {e}")


def main() -> None:
    if config_manager.get("settings.hot_reload", False):
        hot_reloader.start()
    client.set_tick_handler(_tick_handle_expired)
    client.start(debug=DEBUG)



if __name__ == "__main__":
    main()
