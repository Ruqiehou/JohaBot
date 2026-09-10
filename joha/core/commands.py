"""
命令处理器 - 处理所有Bot命令
将命令处理逻辑从main.py分离出来
"""
from typing import Optional
import asyncio

from joha.core.service import message_service
from joha.managers.style_learner import style_learner
from joha.config.config_manager import config
# generator 仅在 /切换模型 命令中延迟导入，避免 LLM 未配置时影响其他管理命令
from joha.core.persona_monitor import persona_monitor
from joha.managers.personas import (
    list_personas, persona_info, switch_persona,
    create_persona, delete_persona, set_group_persona, get_group_persona_name,
)


def is_admin(userid: str) -> bool:
    """检查用户是否为管理员"""
    from joha.managers.admin import admin_manager
    return admin_manager.is_admin(int(userid))


def add_admin(userid: str) -> bool:
    """添加管理员"""
    from joha.managers.admin import admin_manager
    return admin_manager.add_admin(int(userid))


def remove_admin(userid: str) -> bool:
    """删除管理员"""
    from joha.managers.admin import admin_manager
    return admin_manager.remove_admin(int(userid))


def get_admin_list_str() -> str:
    """获取管理员列表字符串"""
    from joha.managers.admin import admin_manager
    admins = admin_manager.get_admins()
    if not admins:
        return "暂无管理员"
    return "\n".join([f"- {admin}" for admin in admins])


def format_duration(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


def format_top_groups(top_groups: list) -> str:
    if not top_groups:
        return "暂无"
    return "，".join([f"{group_id}:{count}" for group_id, count in top_groups])


FALLBACK_COMMAND_ALIASES = {
    "帮助": "/帮助",
    "help": "/帮助",
    "h": "/帮助",
    "全局启动": "/全局启动",
    "全局开启": "/全局启动",
    "全局打开": "/全局启动",
    "启动全局": "/全局启动",
    "开启全局": "/全局启动",
    "全局关闭": "/全局关闭",
    "全局关掉": "/全局关闭",
    "关闭全局": "/全局关闭",
    "本群启动": "/本群启动",
    "本群开启": "/本群启动",
    "本群打开": "/本群启动",
    "启动本群": "/本群启动",
    "开启本群": "/本群启动",
    "本群关闭": "/本群关闭",
    "本群关掉": "/本群关闭",
    "关闭本群": "/本群关闭",
    "模式": "/模式",
    "查看模式": "/模式",
    "当前模式": "/模式",
    "管理员列表": "/管理员列表",
    "admin": "/管理员列表",
    "模型": "/模型",
    "模型列表": "/模型",
    "当前模型": "/当前模型",
    "模型状态": "/模型状态",
    "统计": "/统计",
    "群状态": "/群状态",
    "人设": "/人设列表",
    "人设列表": "/人设列表",
    "人设状态": "/人设",
    "稳定性": "/人设",
}


def normalize_fallback_command(text: str) -> Optional[str]:
    raw = text.strip()
    if not raw:
        return None
    if raw.startswith("/"):
        return raw
    compact = "".join(raw.split())
    if compact in FALLBACK_COMMAND_ALIASES:
        return FALLBACK_COMMAND_ALIASES[compact]
    for prefix in ["添加管理员", "删除管理员", "风格", "清除风格", "切换模型", "人设信息", "切换人设", "绑定人设", "创建人设", "删除人设"]:
        if raw.startswith(prefix + " "):
            return "/" + raw
    return None


class CommandHandler:
    """命令处理器"""
    
    @staticmethod
    async def handle_command(command: str, userid: str, msg_group_id: int, bot_api) -> Optional[str]:
        """
        处理命令
        
        Args:
            command: 命令字符串
            userid: 用户ID
            msg_group_id: 消息所在群ID
            bot_api: Bot API对象
            
        Returns:
            响应消息或None
        """
        parts = command.split(" ", 2)
        cmd = parts[0].lower()
        response = None

        # 所有人可用的命令（反馈类）
        if cmd in ["/好评", "/good"]:
            from joha.decision.group_state import group_state_manager
            from joha.decision.reply_decision import apply_feedback
            group_state_manager.record_feedback(str(msg_group_id), positive=True)
            apply_feedback("chat", positive=True)
            response = "✅ 已记录好评，谢谢你的反馈~"

        elif cmd in ["/差评", "/bad"]:
            from joha.decision.group_state import group_state_manager
            from joha.decision.reply_decision import apply_feedback
            group_state_manager.record_feedback(str(msg_group_id), positive=False)
            apply_feedback("chat", positive=False)
            response = "📝 已记录差评，我会努力改进的"

        elif cmd == "/群状态":
            from joha.decision.group_state import group_state_manager
            from joha.decision.cooldown import cooldown_manager
            gid = str(msg_group_id)
            state = group_state_manager.get(gid)
            cd_stats = cooldown_manager.get_group_stats(gid)
            response = (
                f"📊 群 {msg_group_id} 状态\n"
                f"━━━━━━━━━━━━━━\n"
                f"消息频率: {state.msg_per_minute:.1f} 条/分钟\n"
                f"5分钟均频: {state.msg_per_5min:.1f} 条/分钟\n"
                f"总消息数: {state.total_messages}\n"
                f"机器人回复: {state.bot_replies}\n"
                f"认可率: {state.approval_rate*100:.1f}%\n"
                f"活跃对话: {'是' if state.is_active_conversation else '否'}\n"
                f"距上次回复: {cd_stats['last_reply_seconds_ago']:.0f}秒\n"
                f"━━━━━━━━━━━━━━"
            )

        # 管理员专属命令
        if not is_admin(userid):
            if response:
                await bot_api.send_group_message(group_id=msg_group_id, message=response)
            return response
        
        if cmd in ["/帮助", "/help", "/h"]:
            response = (
                "📖 命令帮助\n"
                "━━━━━━━━━━━━━━\n"
                "💬 对话模式：\n"
                "  /全局启动  - 全局切换到主动模式\n"
                "  /全局关闭  - 全局切换到被动模式\n"
                "  /本群启动  - 本群切换到主动模式\n"
                "  /本群关闭  - 本群切换到被动模式\n"
                "  /模式      - 查看当前全局模式\n"
                "  /模式 群号 - 查看指定群的模式\n"
                "  备用直通：全局启动/全局关闭/本群启动/本群关闭/模式\n\n"
                "👥 管理员：\n"
                "  /管理员列表         - 查看管理员\n"
                "  /添加管理员 QQ号    - 添加管理员\n"
                "  /删除管理员 QQ号    - 删除管理员\n\n"
                "🎨 风格学习：\n"
                "  /风格 QQ号    - 查看用户风格\n"
                "  /清除风格 QQ号 - 清除用户风格数据\n\n"
                "🤖 模型管理：\n"
                "  /模型        - 查看可用对话模型列表\n"
                "  /当前模型    - 查看当前使用的对话模型\n"
                "  /切换模型 名称 - 切换到指定对话模型\n\n"
                "📊 决策反馈：\n"
                "  /好评 - 机器人回复得好\n"
                "  /差评 - 机器人回复多余或不好\n"
                "  /群状态 - 查看当前群决策状态\n\n"
                "🎭 人设管理：\n"
                "  /人设列表          - 查看所有人设\n"
                "  /人设信息 [名称]   - 查看人设详情\n"
                "  /切换人设 名称     - 切换全局活跃人设\n"
                "  /绑定人设 [群号] 名称 - 绑定群到指定人设\n"
                "  /创建人设 名称|显示名|描述 - 创建新人设\n"
                "  /删除人设 名称     - 删除人设（不可删除 joha）\n"
                "  /人设              - 查看人设稳定性报告\n\n"
                "📊 其他：\n"
                "  /统计 - 查看运行统计\n"
                "  /帮助 - 显示此帮助信息"
            )
        
        elif cmd in ["/模式", "/mode"]:
            # 检查是否有群组参数
            if len(parts) >= 2:
                group_id = parts[1]
                mode = message_service.get_group_mode(group_id)
                response = f"群组 {group_id} 的模式：{mode}"
            else:
                response = f"当前全局模式：{message_service.get_global_mode()}"
        
        elif cmd in ["/全局启动"]:
            message_service.set_global_mode("active")
            response = "全局已启动"
        
        elif cmd in ["/全局关闭"]:
            message_service.set_global_mode("passive")
            response = "全局已关闭"
        
        elif cmd in ["/本群启动"]:
            message_service.set_group_mode(str(msg_group_id), "active")
            response = f"本群已启动"
        
        elif cmd in ["/本群关闭"]:
            message_service.set_group_mode(str(msg_group_id), "passive")
            response = f"本群已关闭"
        
        elif cmd in ["/管理员列表", "/admin"]:
            response = f"管理员列表：\n{get_admin_list_str()}"
        
        elif cmd == "/添加管理员" and len(parts) >= 2:
            new_admin = parts[1]
            response = f"已添加管理员：{new_admin}" if add_admin(new_admin) else f"{new_admin} 已经是管理员"
        
        elif cmd == "/删除管理员" and len(parts) >= 2:
            del_admin = parts[1]
            response = f"已删除管理员：{del_admin}" if remove_admin(del_admin) else f"{del_admin} 不是管理员或无法删除"
        
        elif cmd == "/统计":
            stats = message_service.get_stats()
            from joha.decision.group_state import group_state_manager
            gs_stats = group_state_manager.get_stats()
            response = (
                "📊 Joha 运行统计\n"
                "━━━━━━━━━━━━━━\n"
                f"运行时长：{format_duration(stats['uptime'])}\n"
                f"全局模式：{message_service.get_global_mode()}\n"
                f"群配置：活跃{stats['active_groups']}个 / 被动{stats['passive_groups']}个\n\n"
                "💬 消息处理\n"
                f"收到消息：{stats['total_messages']}\n"
                f"学习消息：{stats['learned_messages']}\n"
                f"决策次数：{stats['reply_decisions']}\n"
                f"生成回复：{stats['generated_replies']}\n"
                f"跳过回复：{stats['skipped_replies']}\n"
                f"回复失败：{stats['failed_replies']}\n\n"
                "🧠 决策模型\n"
                f"追踪群组数：{gs_stats['total_groups']}\n"
                f"群总消息：{gs_stats['total_messages']}\n"
                f"机器人回复：{gs_stats['total_bot_replies']}\n"
                f"平均群频：{gs_stats['avg_msg_per_min']:.1f} msg/min\n"
            )
        
        elif cmd == "/风格" and len(parts) >= 2:
            target_user = parts[1]
            style_info = style_learner.styles.get(target_user, {})
            if not style_info:
                response = f"用户 {target_user} 还没有风格数据"
            else:
                msg_count = style_info.get('message_count', 0)
                avg_len = style_info.get('avg_length', 0)
                emoji_rate = style_info.get('emoji_usage', 0)
                response = (
                    f"用户 {target_user} 的风格：\n"
                    f"消息数：{msg_count}\n"
                    f"平均长度：{avg_len:.1f}字\n"
                    f"表情使用率：{emoji_rate*100:.0f}%"
                )
        
        elif cmd == "/清除风格" and len(parts) >= 2:
            target_user = parts[1]
            style_learner.clear_user_style(target_user)
            response = f"已清除用户 {target_user} 的风格数据"

        # ── 工具命令（所有人可用）──
        elif cmd in ["/tools", "/工具列表"]:
            from joha.core.tool_registry import tool_registry
            if not tool_registry._initialized:
                tool_registry.auto_discover()
            tools_desc = tool_registry.get_help_text()
            response = "🛠 可用工具：\n" + ("\n".join(tools_desc.split("\n")[1:]) if "\n" in tools_desc else tools_desc)
        
        elif cmd in ["/search", "/s", "/web_search"]:
            from joha.core.tool_registry import tool_registry
            if not tool_registry._initialized:
                tool_registry.auto_discover()
            query = parts[1] if len(parts) >= 2 else ""
            if not query:
                response = "用法：/search <搜索关键词>"
            else:
                # 工具调用包含同步网络请求，放到线程池执行避免阻塞事件循环
                response = await asyncio.to_thread(tool_registry.dispatch, "search", query)

        elif cmd in ["/webpage", "/wp", "/fetch"]:
            from joha.core.tool_registry import tool_registry
            if not tool_registry._initialized:
                tool_registry.auto_discover()
            url = parts[1] if len(parts) >= 2 else ""
            if not url:
                response = "用法：/webpage <URL>"
            else:
                # 工具调用包含同步网络请求，放到线程池执行避免阻塞事件循环
                response = await asyncio.to_thread(tool_registry.dispatch, "webpage", url)

        # ── 多人设管理 ──
        elif cmd in ["/人设列表", "/personas"]:
            response = list_personas()

        elif cmd in ["/人设信息", "/persona"]:
            name = parts[1] if len(parts) >= 2 else ""
            response = persona_info(name)

        elif cmd == "/切换人设" and len(parts) >= 2:
            name = parts[1]
            if switch_persona(name):
                response = f"✅ 已切换到人设: {name}"
            else:
                response = f"❌ 切换失败：未知人设 '{name}'，使用 /人设列表 查看可用人设"

        elif cmd == "/创建人设" and len(parts) >= 2:
            args = parts[1].split("|", 2)
            name = args[0].strip()
            display_name = args[1].strip() if len(args) >= 2 else ""
            description = args[2].strip() if len(args) >= 3 else ""
            if create_persona(name, display_name=display_name, description=description):
                response = f"✅ 已创建人设: {name}"
            else:
                response = f"❌ 创建失败，人设 '{name}' 可能已存在"

        elif cmd == "/删除人设" and len(parts) >= 2:
            name = parts[1]
            if delete_persona(name):
                response = f"✅ 已删除人设: {name}"
            else:
                response = f"❌ 删除失败，{name} 不存在或为默认人设"

        elif cmd == "/绑定人设" and len(parts) >= 3:
            group_id = parts[1]
            persona_name = parts[2]
            if set_group_persona(group_id, persona_name):
                response = f"✅ 群 {group_id} 已绑定人设: {persona_name}"
            else:
                response = f"❌ 绑定失败：未知人设 '{persona_name}'"

        elif cmd == "/绑定人设" and len(parts) == 2:
            gid = str(msg_group_id)
            pname = get_group_persona_name(gid)
            response = f"本群({gid}) 当前人设: {pname}"

        # ── LLM 模型切换 ──
        elif cmd in ["/模型列表", "/模型"]:
            providers = config.get_llm_providers()
            if not providers:
                response = "暂无可用模型配置"
            else:
                active = config.get_active_provider_name()
                lines = ["🤖 可用模型：", "━━━━━━━━━━━━━━"]
                for p in providers:
                    marker = "✅ " if p.get('name') == active else "   "
                    label = p.get('label', p['name'])
                    model = p.get('model', '')
                    lines.append(f"{marker}{p['name']} - {label} ({model})")
                lines.append("━━━━━━━━━━━━━━")
                lines.append("💡 切换：/切换模型 <名称>")
                response = "\n".join(lines)

        elif cmd == "/当前模型":
            active = config.get_active_provider_name() or "默认"
            provider = config.get_active_provider()
            model = config.llm_model
            response = (
                f"🤖 当前 AI 模型\n"
                f"━━━━━━━━━━━━━━\n"
                f"Provider：{active}\n"
                f"模型：{model}\n"
            )
            if provider and provider.get('label'):
                response += f"名称：{provider['label']}\n"
            if provider:
                response += f"接口：{provider.get('base_url', '')}"

        elif cmd == "/切换模型" and len(parts) >= 2:
            from joha.ai.generator import generator
            target = parts[1]
            if generator.switch_provider(target):
                response = (
                    f"✅ 已切换到模型: {target}\n"
                    f"模型: {config.llm_model}"
                )
            else:
                response = f"❌ 未找到模型: {target}\n使用 /模型 查看可用列表"

        elif cmd == "/模型状态":
            response = "💡 使用 /当前模型 查看当前模型信息，/模型 查看可用列表"

        # ── 人设管理 ──
        elif cmd in ["/人设", "/人设状态", "/稳定性"]:
            response = persona_monitor.format_report()
            # 检查是否有警告
            alerts = persona_monitor.check_and_alert()
            if alerts:
                response += "\n\n" + "\n".join(alerts)

        if response:
            await bot_api.send_group_message(group_id=msg_group_id, message=response)

        return response


# 全局命令处理器实例
command_handler = CommandHandler()