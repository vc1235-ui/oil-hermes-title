"""测试夹具：把插件目录当包加载，让 ``oht_*`` 模块的包内相对导入能正常解析。

插件在 Hermes 里是「目录即包」（`__init__.py` + `register(ctx)`），内部模块用
``from . import ...`` 互相引用；测试要复用这份源码，就必须按包的方式导入，
不能用 ``sys.path`` 平铺导入单文件。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PKG_NAME = "oil_hermes_title_pkg"

if PKG_NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        PKG_NAME, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules[PKG_NAME] = _pkg
    _spec.loader.exec_module(_pkg)

# 短名别名：测试里直接 `from oht_store import ...` 即可
for _name in ("oht_rules", "oht_store", "oht_state", "oht_naming", "oht_engine", "oht_cli", "oht_backfill"):
    sys.modules[_name] = importlib.import_module(f"{PKG_NAME}.{_name}")
