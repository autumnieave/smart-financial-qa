"""pytest 根路径配置：保证仓库根目录可导入（tools/pipelines/eval 等包）"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
