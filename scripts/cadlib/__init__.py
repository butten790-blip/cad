"""cadlib —— CAD 施工图确定性出图引擎。

分层原则（不可违反）：
    模型只负责产出 drawing.spec.json；本包只负责 spec → DXF 的确定性几何生成。
    本包内不得出现任何对模型/网络的调用，保证同输入必然同输出。
"""

__version__ = "0.1.0"

from .context import Context          # noqa: F401
from . import standards               # noqa: F401

__all__ = ["Context", "standards", "__version__"]
