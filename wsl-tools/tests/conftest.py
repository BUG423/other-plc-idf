"""pytest 共享配置：把 wsl-tools 根目录加入 sys.path，便于 import 契约模块与两个脚本。"""

import sys
from pathlib import Path

WSL_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(WSL_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(WSL_TOOLS_DIR))
