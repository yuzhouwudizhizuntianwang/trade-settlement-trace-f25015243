# -*- coding: utf-8 -*-
"""统一响应结构。

所有对外接口都必须返回 {"ok": bool, "data": ..., "error": {...}}。
为什么单独抽一个模块：接口一多，最容易出现"这个接口返回 list、那个返回 {code:0}"，
前端和验证脚本就得写一堆 if。收敛到一处，改结构只改这个文件。
"""

from typing import Any, Optional


def ok(data: Any = None, **extra: Any) -> dict:
    """返回成功结构。

    入参：
        data  —— 业务数据，任意可 JSON 序列化对象，默认 None
        extra —— 额外平铺字段（如 page、total），会并入 data 同级
    出参：
        {"ok": True, "data": <data 或含 extra 的 dict>, "error": None}
    失败行为：
        不抛异常。data 不是可序列化对象时由调用方的 JSON 编码器报错（见 main.py 的异常处理器）。
    """
    # extra 不为空时合进 data，保证前端只认 data 一个入口
    if extra:
        if isinstance(data, dict):
            data = {**data, **extra}          # 字典直接合并
        else:
            data = {"value": data, **extra}   # 非字典包一层，避免覆盖原始值
    return {"ok": True, "data": data, "error": None}


def fail(code: str, msg: str, data: Any = None) -> dict:
    """返回失败结构。

    入参：
        code —— 机器可读错误码，约定用大写+下划线，如 FORBIDDEN、REDLINE_BLOCKED
        msg  —— 给人看的中文说明，不许带堆栈或 SQL
        data —— 可选附加信息（如被拦截的字段名列表）
    出参：
        {"ok": False, "data": <data>, "error": {"code": code, "msg": msg}}
    失败行为：
        code 为空时自动补 "UNKNOWN"，保证前端永远拿得到字符串。
    """
    return {
        "ok": False,
        "data": data,
        "error": {"code": code or "UNKNOWN", "msg": msg or "未提供错误说明"},
    }


def is_ok(payload: dict) -> bool:
    """判断一个响应结构是否为成功。

    入参：payload —— 任意 dict（通常来自接口返回）
    出参：True 表示 ok 为 True 且 error 为 None
    失败行为：payload 不是 dict 或缺少 ok 字段时返回 False，不抛异常（验证脚本里会用到）。
    """
    return isinstance(payload, dict) and payload.get("ok") is True and payload.get("error") is None
