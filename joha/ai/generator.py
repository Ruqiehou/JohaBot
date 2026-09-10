from joha.ai.clients import OpenAICompatibleClient
from joha.managers.history_manager import load_history
from joha.config.config_manager import config
from joha.config.cache import cache_result, response_cache
from joha.managers.style_learner import style_learner
from joha.core.message_builder import message_builder
from joha.core.response_postprocessor import post_processor
from joha.config.logger import tprint
import asyncio
import logging
import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class Generator:
    """核心回复生成器 - 使用统一的 AI 客户端架构，支持动态切换 Provider"""

    def __init__(self):
        self._request_count = 0
        self._error_count = 0
        self._client = None
        self._model = None
        self._init_client()

    def _init_client(self):
        """根据当前配置初始化或更新 AI 客户端"""
        self._client = OpenAICompatibleClient(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            model=config.llm_model,
            enable_tools=False
        )
        self._model = config.llm_model
        tprint("info", f"[Generator] 已加载 AI 客户端 | {config.get_active_provider_name() or '默认'} | 模型: {self._model}")

    def switch_provider(self, name: str) -> bool:
        """切换 LLM Provider 并重建客户端

        Args:
            name: Provider 名称（对应 config.json 中 llm.providers 的 name）

        Returns:
            是否切换成功
        """
        if config.switch_provider(name):
            self._init_client()
            return True
        return False

    def get_available_models(self, role: str = "chat") -> List[Dict]:
        """获取可用的模型列表
        
        Args:
            role: 角色类型 (chat 或 classifier)
            
        Returns:
            模型列表
        """
        return config.get_available_models(role)

    def get_model_names(self, role: str = "chat") -> List[str]:
        """获取模型名称列表
        
        Args:
            role: 角色类型 (chat 或 classifier)
            
        Returns:
            模型名称列表
        """
        return config.get_model_names(role)

    def format_model_list(self, role: str = "chat") -> str:
        """格式化输出模型列表
        
        Args:
            role: 角色类型 (chat 或 classifier)
            
        Returns:
            格式化的模型列表字符串
        """
        models = self.get_available_models(role)
        if not models:
            return "暂无可用的模型"
        
        active_provider = config.get_active_provider_name()
        
        # 构建标题
        role_names = {"chat": "对话模型", "classifier": "分类器模型"}
        title = f"📋 {role_names.get(role, role)}列表 ({len(models)}个)"
        
        lines = [title, "─" * 40]
        for i, m in enumerate(models, 1):
            # 标记当前激活的模型
            if m['name'] == active_provider:
                marker = "✅ "
                suffix = " [当前]"
            else:
                marker = f"{i}. "
                suffix = ""
            
            # 格式化显示：名称 - 标签 (模型名)
            line = f"{marker}{m['name']}"
            if m.get('label') and m['label'] != m['name']:
                line += f" - {m['label']}"
            line += f" ({m['model']}){suffix}"
            lines.append(line)
        
        return "\n".join(lines)

    @property
    def current_model(self) -> str:
        return self._model

    @property
    def current_provider(self) -> str:
        return config.get_active_provider_name() or "默认"

    @cache_result(response_cache, ttl=60)
    def _cached_chat(self, model: str, messages: tuple, temperature: float, max_tokens: int) -> str:
        """缓存的聊天调用"""
        try:
            result = self._client.call_with_context(
                messages=list(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False
            )
            self._request_count += 1
            return result
        except Exception as e:
            raise RuntimeError(f"AI 调用失败: {e}")

    async def chat(self, messages: list, temperature=0.6, max_tokens=1024) -> Optional[str]:
        """异步生成接口"""
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                self._cached_chat,
                self._model,
                tuple(messages),
                temperature,
                max_tokens
            )
            return result
        except Exception as e:
            self._error_count += 1
            logger.error(f"生成失败：{e}", exc_info=True)
            tprint("error", f"[AI] 生成失败：{e}")
            return None

    def chat_sync(self, messages: list, temperature=0.6, max_tokens=1024) -> Optional[str]:
        """同步生成接口（用于工具调用等场景）"""
        try:
            result = self._cached_chat(
                self._model,
                tuple(messages),
                temperature,
                max_tokens
            )
            return result
        except Exception as e:
            self._error_count += 1
            logger.error(f"生成失败：{e}", exc_info=True)
            tprint("error", f"[AI] 生成失败：{e}")
            return None

    def _build_messages(self, question: str, userid: str = None, group_id: Optional[str] = None) -> List[Dict[str, str]]:
        hour = datetime.datetime.now().hour

        if 14 <= hour < 18:
            greeting = "下午好！"
        elif 18 <= hour < 23:
            greeting = "晚上好！"
        else:
            greeting = "你好！"

        return message_builder.build(
            user_id=userid or "unknown",
            message=question,
            persona_name="joha",
            history=load_history(userid, group_id=group_id) if userid else [],
            include_style=True,
            history_limit=5,
            group_id=group_id,
        )

    async def generate(self, question: str, userid: str = None, group_id: Optional[str] = None) -> str:
        """生成回复"""
        messages = self._build_messages(question, userid, group_id=group_id)
        response = await self.chat(messages)

        # 后处理：过滤不符合人设的内容
        if response:
            response = post_processor.process(response)

        # 学习用户风格
        if userid:
            style_learner.learn_from_message(userid, question)

        return response


generator = Generator()
