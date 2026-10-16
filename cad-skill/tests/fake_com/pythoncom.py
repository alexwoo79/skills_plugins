"""假的 pythoncom（仅测试用）：只需覆盖 cad_core 用到的常量与 CoInitialize。"""

VT_ARRAY = 8192
VT_R8 = 5
VT_DISPATCH = 9


def CoInitialize(*_args, **_kwargs):
    return None


def CoUninitialize():
    return None
