"""供应商适配器。

新增供应商的完整步骤：
1. 在本目录写一个适配器类（若是 OpenAI 兼容协议，继承
   :class:`~bzp_workbench.services.llm.adapters.openai_compat.OpenAICompatAdapter`
   并只声明默认值即可）；
2. 在 ``services/llm/registry.py`` 的 ``_ADAPTERS`` 里注册；
3. 前端无需改动 —— 供应商下拉与能力选项会自动出现。
"""

from .deepseek import DeepSeekAdapter
from .openai_compat import OpenAICompatAdapter

__all__ = ["DeepSeekAdapter", "OpenAICompatAdapter"]
