"""假的 win32com.client（仅测试用）。

GetActiveObject 返回同一个假 CAD 应用单例，因此 CLI 子进程里的
连接、绘图、脚本执行都能在无 Windows、无 CAD 的环境下跑通。
"""

from __future__ import annotations

from fake_doc import get_active_app


def VARIANT(_vt, value):
    """假 VARIANT：直接返回原值，便于断言坐标数组"""
    return value


def GetActiveObject(*_args, **_kwargs):
    return get_active_app()


def Dispatch(*_args, **_kwargs):
    return get_active_app()
