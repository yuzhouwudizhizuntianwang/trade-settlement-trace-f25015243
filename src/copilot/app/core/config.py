# -*- coding: utf-8 -*-
"""配置加载：config.yaml + 环境变量注入。

为什么配置要单独一层：
1. 密钥、模型地址一旦写死在代码里，换环境必然改代码，改代码就可能改坏逻辑；
2. 配置缺失时必须在启动阶段就失败（fail fast），不能等第一次调用模型才报错。
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")   # 只认 ${NAME} 这种整字段引用


def _resolve_env(value: Any) -> Any:
    """把配置里的 ${ENV_NAME} 替换成环境变量值（递归处理 dict / list / 标量）。

    入参：value —— 从 YAML 读出来的任意节点
    出参：替换后的同结构数据；环境变量不存在时返回空字符串（后续由校验决定是否报错）
    失败行为：
        不抛异常。环境变量缺失不在这里报错，留到 validate() 统一检查，便于一次报全。
    """
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value.strip())     # 只有"整个值就是一个 ${X}"才替换
        if match:
            return os.environ.get(match.group(1), "")  # 缺失时给空串，交给校验
    return value


def _load_yaml(path: Path) -> dict:
    """读取 YAML 文件。

    入参：path —— 配置文件路径
    出参：解析后的 dict
    失败行为：
        文件不存在抛 FileNotFoundError；PyYAML 未安装抛 RuntimeError 并提示安装命令；
        YAML 语法错误原样抛出（PyYAML 的报错比我们自己包装的更准）。
    """
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}")
    try:
        import yaml                                  # 延迟导入：让纯逻辑单测不需要 PyYAML
    except ImportError as exc:
        raise RuntimeError("缺少依赖 PyYAML，请执行：pip install pyyaml") from exc
    with path.open("r", encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


class Settings:
    """配置容器。

    只做两件事：读取 + 校验；不做任何业务判断（业务阈值放 config 里，代码不解释）。
    """

    def __init__(self, raw: dict):
        """构造配置对象。

        入参：raw —— 已做环境变量替换的配置字典
        出参：无
        失败行为：缺关键节点时抛 KeyError，由 get_settings() 统一转成可读报错。
        """
        self.raw = raw

    # ---- 便捷取值：全部用点号路径，避免到处写 raw["llm"]["base_url"] ----
    def get(self, path: str, default: Any = None) -> Any:
        """按 "a.b.c" 路径取配置。

        入参：path —— 点号路径；default —— 取不到时的默认值
        出参：配置值
        失败行为：路径不存在且未给默认值时返回 None，不抛异常。
        """
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def validate(self, required_paths: list[str]) -> list[str]:
        """校验必填配置是否已注入。

        入参：required_paths —— 必填项的点号路径列表
        出参：缺失项列表（空列表表示全齐）
        失败行为：不抛异常，由调用方决定是启动失败还是仅告警。
        """
        missing = []
        for path in required_paths:
            value = self.get(path)
            if value is None or value == "":          # 空串视为未注入（环境变量缺失就是空串）
                missing.append(path)
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """取全局配置单例（带缓存，重复调用不重复读盘）。

    入参：无（配置文件路径可用环境变量 COPILOT_CONFIG 覆盖）
    出参：Settings 实例
    失败行为：
        文件缺失或 YAML 非法时抛异常——服务启动即失败，不带着坏配置跑。
    """
    config_path = Path(os.environ.get("COPILOT_CONFIG", "config.yaml"))
    raw = _resolve_env(_load_yaml(config_path))
    return Settings(raw)
