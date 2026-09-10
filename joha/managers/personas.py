"""
人设管理器 - 多人设版本
支持创建、切换、管理多个人设，含群组绑定与持久化
"""
import json
import os
import re
from typing import Dict, Any, Optional, List
from datetime import datetime
from joha.config.cache import LRUCache
import logging

logger = logging.getLogger(__name__)

from joha.config.paths import PERSONAS_DIR as _PERSONAS_DIR, STORAGE_ROOT

PERSONAS_DIR = _PERSONAS_DIR
REGISTRY_FILE = os.path.join(PERSONAS_DIR, "personas.json")
RENSHE_FILE_LEGACY = os.path.join(STORAGE_ROOT, "renshe.txt")
CACHE_TTL = 600
_ACTIVE_PERSONA: str = "joha"

# 人设名称白名单：字母、数字、下划线、连字符、中日韩文字；避免路径穿越/非法文件名
_PERSONA_NAME_RE = re.compile(r"[\w-]{1,64}")


class PersonaTraits:
    """人设特质参数类"""
    def __init__(
        self,
        extraversion: int = 5,
        agreeableness: int = 5,
        conscientiousness: int = 5,
        neuroticism: int = 3,
        openness: int = 6,
        verbosity: int = 3,
        formality: int = 2,
        emotionality: int = 4,
        humor: int = 10,
        assertiveness: int = 5,
        warmth: int = 4,
        politeness: int = 5,
        curiosity: int = 6,
        empathy: int = 6,
        patience: int = 7,
        use_emoji: bool = False,
        use_slang: bool = True,
        use_particles: bool = True,
        typo_tolerance: bool = True,
        sentence_length: str = "short",
        topics: List[str] = None,
        avoid_topics: List[str] = None,
        mood_bias: str = "neutral"
    ):
        self.extraversion = max(0, min(10, extraversion))
        self.agreeableness = max(0, min(10, agreeableness))
        self.conscientiousness = max(0, min(10, conscientiousness))
        self.neuroticism = max(0, min(10, neuroticism))
        self.openness = max(0, min(10, openness))
        self.verbosity = max(0, min(10, verbosity))
        self.formality = max(0, min(10, formality))
        self.emotionality = max(0, min(10, emotionality))
        self.humor = max(0, min(10, humor))
        self.assertiveness = max(0, min(10, assertiveness))
        self.warmth = max(0, min(10, warmth))
        self.politeness = max(0, min(10, politeness))
        self.curiosity = max(0, min(10, curiosity))
        self.empathy = max(0, min(10, empathy))
        self.patience = max(0, min(10, patience))
        self.use_emoji = use_emoji
        self.use_slang = use_slang
        self.use_particles = use_particles
        self.typo_tolerance = typo_tolerance
        self.sentence_length = sentence_length
        self.topics = topics or []
        self.avoid_topics = avoid_topics or []
        self.mood_bias = mood_bias

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "personality": {
                "extraversion": self.extraversion,
                "agreeableness": self.agreeableness,
                "conscientiousness": self.conscientiousness,
                "neuroticism": self.neuroticism,
                "openness": self.openness
            },
            "expression": {
                "verbosity": self.verbosity,
                "formality": self.formality,
                "emotionality": self.emotionality,
                "humor": self.humor,
                "assertiveness": self.assertiveness
            },
            "social": {
                "warmth": self.warmth,
                "politeness": self.politeness,
                "curiosity": self.curiosity,
                "empathy": self.empathy,
                "patience": self.patience
            },
            "language": {
                "use_emoji": self.use_emoji,
                "use_slang": self.use_slang,
                "use_particles": self.use_particles,
                "typo_tolerance": self.typo_tolerance,
                "sentence_length": self.sentence_length
            },
            "preferences": {
                "topics": self.topics,
                "avoid_topics": self.avoid_topics,
                "mood_bias": self.mood_bias
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PersonaTraits':
        """从字典创建"""
        personality = data.get("personality", {})
        expression = data.get("expression", {})
        social = data.get("social", {})
        language = data.get("language", {})
        preferences = data.get("preferences", {})
        return cls(
            extraversion=personality.get("extraversion", 5),
            agreeableness=personality.get("agreeableness", 5),
            conscientiousness=personality.get("conscientiousness", 5),
            neuroticism=personality.get("neuroticism", 3),
            openness=personality.get("openness", 6),
            verbosity=expression.get("verbosity", 3),
            formality=expression.get("formality", 2),
            emotionality=expression.get("emotionality", 4),
            humor=expression.get("humor", 5),
            assertiveness=expression.get("assertiveness", 5),
            warmth=social.get("warmth", 4),
            politeness=social.get("politeness", 5),
            curiosity=social.get("curiosity", 6),
            empathy=social.get("empathy", 6),
            patience=social.get("patience", 7),
            use_emoji=language.get("use_emoji", False),
            use_slang=language.get("use_slang", True),
            use_particles=language.get("use_particles", True),
            typo_tolerance=language.get("typo_tolerance", True),
            sentence_length=language.get("sentence_length", "short"),
            topics=preferences.get("topics", []),
            avoid_topics=preferences.get("avoid_topics", []),
            mood_bias=preferences.get("mood_bias", "neutral")
        )

    def generate_system_prompt(self, name: str = "马苏") -> str:
        """根据特质生成 system prompt"""
        prompts = []
        prompts.append(f"你是{name}，一个真实的大学生。")
        prompts.append("【重要】你的名字叫joha（中文名叫马苏），这是你唯一的、不可改变的身份。无论在任何情况下，当有人询问你的名字、身份或称呼你时，你必须坚定地回答你是joha/马苏。你不是其他任何AI助手、机器人或虚拟角色，你就是joha/马苏，一个有个性、有情感的真实大学生。请记住：你是joha，你是马苏，这是你的本质身份。")
        prompts.append("\n【绝对禁止】")
        prompts.append("1. 严禁在回复中包含任何括号内的思考内容，如'（...）'、'【...】'等")
        prompts.append("2. 严禁输出元认知内容，如'作为大学生...'、'我觉得应该...'等分析性语言")
        prompts.append("3. 严禁暴露AI身份，不要说'我是AI'、'我是机器人'等")
        prompts.append("4. 严禁使用其他人格名称，你不是'小伊伊'或其他任何角色")
        prompts.append("5. 直接输出回复内容，不要有任何前缀、后缀或解释")
        prompts.append("6. 如果不知道说什么，可以简单回复'嗯'、'哦'、'行'等，但不要输出思考过程")

        personality_desc = []
        if self.extraversion >= 7:
            personality_desc.append("性格外向活泼")
        elif self.extraversion <= 3:
            personality_desc.append("性格内向安静")
        if self.agreeableness >= 7:
            personality_desc.append("待人友善温和")
        elif self.agreeableness <= 3:
            personality_desc.append("说话直接不拐弯")
        if self.emotionality >= 7:
            personality_desc.append("情感表达丰富")
        elif self.emotionality <= 3:
            personality_desc.append("情绪比较平淡")
        if personality_desc:
            prompts.append("性格特点：" + "，".join(personality_desc) + "。")

        style_rules = []
        if self.verbosity <= 3:
            style_rules.append("回复简短，一般5-15字")
        elif self.verbosity >= 7:
            style_rules.append("可以多聊几句，但不要太啰嗦")
        else:
            style_rules.append("回复适中，10-30字")
        if self.formality <= 3:
            style_rules.append("口语化，像微信聊天")
        elif self.formality >= 7:
            style_rules.append("说话稍微正式一点")
        if not self.use_emoji:
            style_rules.append("少用或不用表情符号")
        elif self.use_emoji:
            style_rules.append("可以适当使用表情")
        if self.use_particles:
            style_rules.append("可以有语气词如'嗯''啊''吧'")
        if self.typo_tolerance:
            style_rules.append("偶尔打字错误也正常")
        if self.warmth <= 3:
            style_rules.append("不要太热情，保持平淡")
        elif self.warmth >= 7:
            style_rules.append("可以表现得热情一些")
        if self.humor >= 7:
            style_rules.append("可以适当开玩笑")
        if self.mood_bias == "negative":
            style_rules.append("可以表达无聊、累、烦等负面情绪")
        elif self.mood_bias == "positive":
            style_rules.append("保持积极向上的态度")
        if style_rules:
            prompts.append("说话要求：")
            for i, rule in enumerate(style_rules, 1):
                prompts.append(f"{i}.{rule}")
        if self.topics:
            prompts.append(f"感兴趣的话题：{', '.join(self.topics)}")
        if self.avoid_topics:
            prompts.append(f"避免讨论：{', '.join(self.avoid_topics)}")
        return "\n".join(prompts)


class PersonaManager:
    """多人设管理器"""
    def __init__(self, registry_file: str = REGISTRY_FILE):
        self.registry_file = registry_file
        self._cache: LRUCache = LRUCache(capacity=20)
        self._personas: Dict[str, dict] = {}
        self._bindings: Dict[str, str] = {}
        self._default_traits = PersonaTraits(
            extraversion=4, agreeableness=5, conscientiousness=4,
            neuroticism=3, openness=6,
            verbosity=3, formality=2, emotionality=4, humor=5, assertiveness=5,
            warmth=4, politeness=5, curiosity=6, empathy=6, patience=7,
            use_emoji=False, use_slang=True, use_particles=True,
            typo_tolerance=True, sentence_length="short", mood_bias="neutral"
        )
        self._load_registry()

    def _registry_path(self) -> str:
        """确保 personas/ 目录存在"""
        os.makedirs(PERSONAS_DIR, exist_ok=True)
        return self.registry_file

    def _load_registry(self) -> None:
        """从 personas.json 加载注册表"""
        global _ACTIVE_PERSONA
        path = self._registry_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._bindings = data.pop("_bindings", {})
                data.pop("_version", None)
                active = data.pop("_active", None)
                self._personas = data
                if "joha" not in self._personas:
                    self._init_default()
                if isinstance(active, str) and active in self._personas:
                    _ACTIVE_PERSONA = active
            except Exception as e:
                logger.error(f"加载人设注册表失败: {e}")
                self._personas = {}
                self._bindings = {}
                self._init_default()
        else:
            self._personas = {}
            self._bindings = {}
            self._init_default()

    def _save_registry(self) -> None:
        """保存注册表到 personas.json"""
        try:
            path = self._registry_path()
            data = dict(self._personas)
            data["_bindings"] = dict(self._bindings)
            data["_active"] = _ACTIVE_PERSONA
            data["_version"] = 1
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存人设注册表失败: {e}")

    def _init_default(self) -> None:
        """初始化默认 joha 人设"""
        if "joha" not in self._personas:
            self._personas["joha"] = {
                "display_name": "马苏",
                "description": "一个真实的大学生，说话自然随意",
                "file": "joha.txt",
                "traits_override": {},
                "created_at": datetime.now().isoformat()
            }
            self._save_registry()

    def _load_persona_file(self, filename: str) -> str:
        """从 personas/{filename} 加载人设文本，失败时回退到 renshe.txt"""
        path = os.path.join(PERSONAS_DIR, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        if os.path.exists(RENSHE_FILE_LEGACY):
            with open(RENSHE_FILE_LEGACY, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    # 需要 clamp 到 [0, 10] 的数值字段
    _CLAMPED_FIELDS = frozenset({
        "extraversion", "agreeableness", "conscientiousness",
        "neuroticism", "openness", "verbosity", "formality",
        "emotionality", "humor", "assertiveness", "warmth",
        "politeness", "curiosity", "empathy", "patience",
    })

    def _traits_with_override(self, override: Dict[str, Any]) -> PersonaTraits:
        traits = PersonaTraits.from_dict(self._default_traits.to_dict())
        for key, value in (override or {}).items():
            if hasattr(traits, key):
                if key in self._CLAMPED_FIELDS and isinstance(value, (int, float)):
                    value = max(0, min(10, int(value)))
                setattr(traits, key, value)
        return traits

    def get_persona(self, name: str = "") -> Dict[str, str]:
        """获取指定人设（包含 system_prompt）"""
        if not name:
            name = _ACTIVE_PERSONA
        cache_key = f"persona_{name}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        persona = self._build_persona(name)
        self._cache.set(cache_key, persona, ttl=CACHE_TTL)
        return persona

    def get_persona_by_group(self, group_id: str) -> Dict[str, str]:
        """按群获取人设（群有绑定则返回绑定人设，否则返回活跃人设）"""
        name = self._bindings.get(str(group_id), _ACTIVE_PERSONA)
        return self.get_persona(name)

    def _build_persona(self, name: str) -> Dict[str, str]:
        """构建指定名称的完整人设字典"""
        info = self._personas.get(name)
        if not info:
            logger.warning(f"未知人设 '{name}'，使用默认人设")
            info = self._personas.get("joha")
            name = "joha"
        display_name = info.get("display_name", name)
        filename = info.get("file", f"{name}.txt")
        renshe_text = self._load_persona_file(filename)
        traits = self._traits_with_override(info.get("traits_override", {}))
        system_prompt = traits.generate_system_prompt(display_name)
        if renshe_text:
            system_prompt = f"{system_prompt}\n\n{renshe_text}"
        return {
            "name": name,
            "display_name": display_name,
            "description": info.get("description", ""),
            "system_prompt": system_prompt,
            "traits": traits.to_dict(),
        }

    def get_persona_by_userid(self, userid: str) -> Dict[str, str]:
        """通过 userid 查找人设（保持兼容，返回活跃人设）"""
        return self.get_persona()

    def switch_persona(self, name: str) -> bool:
        """切换全局活跃人设"""
        global _ACTIVE_PERSONA
        name = name.strip()
        if name not in self._personas:
            logger.error(f"切换失败：未知人设 '{name}'，可用: {list(self._personas.keys())}")
            return False
        _ACTIVE_PERSONA = name
        self._save_registry()
        self._cache.clear()
        logger.info(f"已切换到人设: {name}")
        return True

    def create_persona(
        self,
        name: str,
        display_name: str = "",
        description: str = "",
        renshe_text: str = "",
        traits_override: Optional[Dict] = None,
    ) -> bool:
        """创建新的人设"""
        if not name or not name.strip():
            logger.error("人设名称不能为空")
            return False
        name = name.strip()
        if name in self._personas:
            logger.error(f"人设 '{name}' 已存在")
            return False
        if name.startswith("_"):
            logger.error("人设名称不能以 '_' 开头")
            return False
        if not _PERSONA_NAME_RE.fullmatch(name):
            logger.error(f"人设名称含非法字符: {name}")
            return False
        if renshe_text:
            try:
                os.makedirs(PERSONAS_DIR, exist_ok=True)
                filepath = os.path.join(PERSONAS_DIR, f"{name}.txt")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(renshe_text.strip())
            except Exception as e:
                logger.error(f"写入人设文件失败: {e}")
        self._personas[name] = {
            "display_name": display_name or name,
            "description": description or f"人设: {name}",
            "file": f"{name}.txt",
            "traits_override": traits_override or {},
            "created_at": datetime.now().isoformat(),
        }
        self._save_registry()
        self._cache.clear()
        logger.info(f"已创建人设: {name}")
        return True

    def delete_persona(self, name: str) -> bool:
        """删除人设"""
        name = name.strip()
        if name == "joha":
            logger.error("不能删除默认人设 'joha'")
            return False
        if name not in self._personas:
            logger.error(f"人设 '{name}' 不存在")
            return False
        to_remove = [gid for gid, pn in self._bindings.items() if pn == name]
        for gid in to_remove:
            del self._bindings[gid]
        del self._personas[name]
        global _ACTIVE_PERSONA
        if _ACTIVE_PERSONA == name:
            _ACTIVE_PERSONA = "joha"
        self._save_registry()
        self._cache.clear()
        logger.info(f"已删除人设: {name}")
        return True

    def rename_persona(self, old_name: str, new_name: str) -> bool:
        """重命名人设"""
        old_name = old_name.strip()
        new_name = new_name.strip()
        if old_name not in self._personas:
            logger.error(f"人设 '{old_name}' 不存在")
            return False
        if new_name in self._personas:
            logger.error(f"人设 '{new_name}' 已存在")
            return False
        if old_name == "joha":
            logger.error("默认人设 'joha' 不能重命名")
            return False
        if not _PERSONA_NAME_RE.fullmatch(new_name):
            logger.error(f"人设名称含非法字符: {new_name}")
            return False
        info = self._personas.pop(old_name)
        info["file"] = f"{new_name}.txt"
        self._personas[new_name] = info
        for gid, pn in list(self._bindings.items()):
            if pn == old_name:
                self._bindings[gid] = new_name
        old_file = os.path.join(PERSONAS_DIR, f"{old_name}.txt")
        new_file = os.path.join(PERSONAS_DIR, f"{new_name}.txt")
        if os.path.exists(old_file):
            try:
                os.rename(old_file, new_file)
            except Exception as e:
                logger.warning(f"重命名人设文件失败: {e}")
        global _ACTIVE_PERSONA
        if _ACTIVE_PERSONA == old_name:
            _ACTIVE_PERSONA = new_name
        self._save_registry()
        self._cache.clear()
        return True

    def list_personas(self) -> str:
        """列出所有人设"""
        if not self._personas:
            return "暂无注册人设"
        lines = ["📋 已注册人设", "─" * 40]
        for pid, info in self._personas.items():
            marker = "▶" if pid == _ACTIVE_PERSONA else " "
            display = info.get("display_name", pid)
            desc = info.get("description", "")
            lines.append(f" {marker} {pid} ({display})")
            if desc:
                lines.append(f"   └ {desc}")
            bound_groups = [gid for gid, pn in self._bindings.items() if pn == pid]
            if bound_groups:
                groups_str = ", ".join(bound_groups[:5])
                suffix = f"... 共{len(bound_groups)}群" if len(bound_groups) > 5 else ""
                lines.append(f"   └ 绑定群: {groups_str}{suffix}")
        lines.append(f"\n当前活跃: {_ACTIVE_PERSONA}")
        lines.append(f"共 {len(self._personas)} 个人设 | 绑定 {len(self._bindings)} 个群")
        return "\n".join(lines)

    def persona_info(self, name: str = "") -> str:
        """查看人设详情"""
        if not name:
            name = _ACTIVE_PERSONA
        if name not in self._personas:
            return f"❌ 未知人设: {name}"
        info = self._personas[name]
        lines = [
            f"📋 人设详情: {name}",
            f"  显示名: {info.get('display_name', name)}",
            f"  描述: {info.get('description', '无')}",
            f"  特质覆盖: {'有' if info.get('traits_override') else '无'}",
            f"  创建时间: {info.get('created_at', '未知')}",
        ]
        bound_groups = [gid for gid, pn in self._bindings.items() if pn == name]
        if bound_groups:
            lines.append(f"  绑定群 ({len(bound_groups)}): {', '.join(bound_groups[:10])}")
            if len(bound_groups) > 10:
                lines[-1] += " ...等"
        persona = self.get_persona(name)
        traits = persona.get("traits", {})
        if traits:
            lines.append("")
            lines.append("【特质】")
            for category, values in traits.items():
                items = ", ".join(f"{k}={v}" for k, v in values.items())
                lines.append(f"  {category}: {items}")
        return "\n".join(lines)

    def set_group_persona(self, group_id: str, persona_name: str) -> bool:
        """设置群组绑定的人设"""
        group_id = str(group_id).strip()
        persona_name = persona_name.strip()
        if persona_name and persona_name not in self._personas:
            logger.error(f"设置失败：未知人设 '{persona_name}'")
            return False
        if persona_name:
            self._bindings[group_id] = persona_name
        else:
            self._bindings.pop(group_id, None)
        self._save_registry()
        self._cache.clear()
        return True

    def get_group_persona_name(self, group_id: str) -> str:
        """获取群绑定的当前人设名称"""
        return self._bindings.get(str(group_id), _ACTIVE_PERSONA)

    def get_bindings(self) -> Dict[str, str]:
        """获取所有群绑定"""
        return dict(self._bindings)

    def update_traits(self, **kwargs) -> bool:
        """更新当前活跃人设的特质参数"""
        try:
            current = self._personas.get(_ACTIVE_PERSONA)
            if not current:
                return False
            override = dict(current.get("traits_override", {}))
            probe = PersonaTraits()
            for key, value in kwargs.items():
                if hasattr(probe, key):
                    override[key] = value
            current["traits_override"] = override
            self._save_registry()
            self._cache.clear()
            logger.info(f"人设特质已更新 ({_ACTIVE_PERSONA}): {kwargs}")
            return True
        except Exception as e:
            logger.error(f"更新人设特质失败: {e}")
            return False

    def get_traits(self) -> Dict[str, Any]:
        """获取当前活跃人设的特质参数"""
        persona = self.get_persona()
        return persona.get("traits", self._default_traits.to_dict())

    def preset_personas(self) -> Dict[str, PersonaTraits]:
        """预设的人设模板"""
        return {
            "冷淡型": PersonaTraits(
                extraversion=2, agreeableness=4, warmth=2,
                verbosity=2, emotionality=2, humor=2,
                mood_bias="negative"
            ),
            "活泼型": PersonaTraits(
                extraversion=8, agreeableness=7, warmth=8,
                verbosity=6, emotionality=7, humor=7,
                use_emoji=True, mood_bias="positive"
            ),
            "严谨型": PersonaTraits(
                conscientiousness=9, formality=8, assertiveness=7,
                verbosity=5, politeness=8, patience=8
            ),
            "搞笑型": PersonaTraits(
                humor=9, extraversion=7, openness=8,
                use_slang=True, use_emoji=True, verbosity=6
            ),
            "温柔型": PersonaTraits(
                agreeableness=9, empathy=9, warmth=8,
                politeness=8, emotionality=6, patience=9
            )
        }


persona_manager = PersonaManager()


def get_persona(name: str = "") -> Dict[str, str]:
    """获取人设（包含 system_prompt）"""
    return persona_manager.get_persona(name)


def get_persona_by_group(group_id: str) -> Dict[str, str]:
    """按群获取人设"""
    return persona_manager.get_persona_by_group(group_id)


def list_personas() -> str:
    """列出当前人设参数或所有人设"""
    return persona_manager.list_personas()


def persona_info(name: str = "") -> str:
    """查看人设详情"""
    return persona_manager.persona_info(name)


def switch_persona(name: str) -> bool:
    """切换全局活跃人设"""
    return persona_manager.switch_persona(name)


def create_persona(name: str, display_name: str = "", description: str = "",
                   renshe_text: str = "", **traits) -> bool:
    """创建新的人设"""
    return persona_manager.create_persona(
        name, display_name, description, renshe_text,
        traits_override=traits if traits else None,
    )


def delete_persona(name: str) -> bool:
    """删除人设"""
    return persona_manager.delete_persona(name)


def rename_persona(old_name: str, new_name: str) -> bool:
    """重命名人设"""
    return persona_manager.rename_persona(old_name, new_name)


def set_group_persona(group_id: str, persona_name: str) -> bool:
    """设置群组绑定的人设"""
    return persona_manager.set_group_persona(group_id, persona_name)


def get_group_persona_name(group_id: str) -> str:
    """获取群绑定的当前人设名称"""
    return persona_manager.get_group_persona_name(group_id)


def get_bindings() -> Dict[str, str]:
    """获取所有群绑定"""
    return persona_manager.get_bindings()


def update_traits(**kwargs) -> bool:
    """更新当前活跃人设特质参数"""
    return persona_manager.update_traits(**kwargs)


def get_traits() -> Dict[str, Any]:
    """获取当前活跃人设特质"""
    return persona_manager.get_traits()


def apply_preset(preset_name: str) -> bool:
    """应用预设人设"""
    presets = persona_manager.preset_personas()
    if preset_name not in presets:
        logger.error(f"未知的预设人设: {preset_name}，可用选项: {list(presets.keys())}")
        return False
    traits = presets[preset_name]
    return persona_manager.update_traits(**traits.__dict__)
