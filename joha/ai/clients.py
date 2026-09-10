"""
统一的 AI 客户端架构
支持多种 AI Provider，提供统一的调用接口
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from openai import OpenAI
import json
import time
import random
from joha.config.logger import tprint


class BaseAIClient(ABC):
    """AI 客户端基类"""

    # 单次请求超时时间（秒），避免网络异常时长时间挂起
    DEFAULT_TIMEOUT: float = 60.0

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.model = model

    @abstractmethod
    def call_with_context(self, messages: List[Dict], **kwargs) -> str:
        """调用 AI 并返回回复"""
        pass

    def _call_with_retry(self, fn, max_retries: int = 3, base_delay: float = 1.0) -> str:
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return fn()
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    tprint("warning", f"[Retry] 第{attempt+1}次重试，{delay:.1f}s后...")
                    time.sleep(delay)
        raise RuntimeError(f"AI 调用失败（重试{max_retries}次后）: {last_error}")


class OpenAICompatibleClient(BaseAIClient):
    """OpenAI 兼容的客户端实现"""
    
    def __init__(self, api_key: str, base_url: str, model: str, enable_tools: bool = False):
        super().__init__(api_key, base_url, model)
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=self.DEFAULT_TIMEOUT)
        self.enable_tools = enable_tools
        self.tools = []
        self.tool_handlers = {}
    
    def register_tool(self, tool_definition: Dict, handler: callable):
        """注册工具"""
        self.tools.append(tool_definition)
        self.tool_handlers[tool_definition["function"]["name"]] = handler
    
    def call_with_context(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        stream: bool = False,
        **kwargs
    ) -> str:
        """调用 AI 并返回回复（带指数退避重试）"""
        def _do_call():
            call_kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": stream,
            }

            if self.enable_tools and self.tools:
                call_kwargs["tools"] = self.tools
                call_kwargs["tool_choice"] = "auto"

            response = self.client.chat.completions.create(**call_kwargs)

            if stream:
                return self._handle_stream_response(response)
            else:
                return self._handle_response(response, messages, temperature, max_tokens)

        return self._call_with_retry(_do_call)
    
    def _handle_stream_response(self, response) -> str:
        """处理流式响应"""
        full = ""
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                content = chunk.choices[0].delta.content
                full += content
        return full
    
    def _handle_response(self, response, messages: List[Dict], temperature: float, max_tokens: int) -> str:
        """处理普通响应（包括工具调用）"""
        message = response.choices[0].message
        
        # 检查是否有工具调用
        if hasattr(message, "tool_calls") and message.tool_calls:
            return self._handle_tool_calls(message.tool_calls, messages, temperature, max_tokens)
        
        return message.content
    
    def _handle_tool_calls(self, tool_calls, messages: List[Dict], temperature: float, max_tokens: int) -> str:
        """处理工具调用"""
        # 添加助手的工具调用消息
        tool_call_messages = []
        for tool_call in tool_calls:
            tool_call_messages.append({
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments
                }
            })
        
        # 构建助手消息
        assistant_msg = {
            "role": "assistant",
            "tool_calls": tool_call_messages
        }
        
        messages.append(assistant_msg)
        
        # 执行工具并添加结果
        for tool_call in tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except (json.JSONDecodeError, TypeError) as e:
                tprint("warning", f"[Tool] 解析 {fn_name} 参数失败: {e}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"工具参数解析失败: {e}"
                })
                continue

            # 执行工具
            if fn_name in self.tool_handlers:
                try:
                    result = self.tool_handlers[fn_name](fn_args)
                except Exception as e:
                    tprint("warning", f"[Tool] 执行 {fn_name} 失败: {e}")
                    result = f"工具执行失败: {e}"
            else:
                result = f"未知工具: {fn_name}"
            
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result)
            })
        
        # 再次调用 AI 生成最终回复
        second_response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        return second_response.choices[0].message.content or ""


class SimpleClassifierClient(BaseAIClient):
    """简单的分类器客户端（用于问题分类等简单任务）"""
    
    def __init__(self, api_key: str, base_url: str, model: str):
        super().__init__(api_key, base_url, model)
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=self.DEFAULT_TIMEOUT)
    
    def call_with_context(
        self,
        messages: List[Dict],
        temperature: float = 0.3,
        max_tokens: int = 10,
        **kwargs
    ) -> str:
        """调用 AI 进行分类（带重试）"""
        def _do_call():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            content = response.choices[0].message.content
            if content is None:
                raise RuntimeError("AI 返回内容为空")
            return content.strip()

        return self._call_with_retry(_do_call)


def create_client_from_provider(provider, client_type: str = "chat", enable_tools: bool = False) -> BaseAIClient:
    """
    从 Provider 对象创建 AI 客户端
    
    Args:
        provider: Provider 对象
        client_type: 客户端类型 ("chat" 或 "classifier")
        enable_tools: 是否启用工具（仅对 chat 类型有效）
    
    Returns:
        AI 客户端实例
    """
    if client_type == "classifier":
        return SimpleClassifierClient(
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=provider.model
        )
    else:
        return OpenAICompatibleClient(
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=provider.model,
            enable_tools=enable_tools
        )
