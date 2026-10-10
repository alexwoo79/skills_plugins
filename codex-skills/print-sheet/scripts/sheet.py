#!/usr/bin/env python3
"""把 Markdown / 源码 / 文本文件排成一份可打印、可预览的 A4 稿。

产物：<name>.html（打印样式，可直接在浏览器里预览/打印）、
      <name>.pdf （送打印机的成品）、<name>-N.png（逐页预览图）。

HTML 支持明暗主题（--theme light|dark|auto）和内置语法高亮（--no-highlight 可关），
--html-only 只出 HTML；主题与高亮的配色在下面的 THEME_VARS / TOKEN_COLORS 里改。
代码区（含 Markdown 围栏/行内代码）里的汉字走楷体、西文仍是等宽，
注释里的西文再叠一层细斜体（--no-comment-style 可关）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from string import Template

MARKDOWN_EXT = {".md", ".markdown", ".mdown", ".mkd", ".mdx"}
DEFAULT_FONT = (
    '"Noto Sans Mono CJK SC", "Noto Sans Mono CJK TC", "JetBrainsMono Nerd Font", '
    '"DejaVu Sans Mono", monospace'
)
MAC_FONT = '"SF Mono", Menlo, "PingFang SC", "Songti SC", monospace'
# 代码区的中文交给楷体：只列各方系统自带的族名（macOS: Kaiti SC/STKaiti；
# Windows: KaiTi；Linux: AR PL UKai / TW-Kai），不默认绑定第三方下载字体。
# 想强制某个字体（比如霞鹜文楷），用 --cjk-font 把它加在最前面。
CJK_FAMILIES = (
    "Kaiti SC", "Kaiti TC", "STKaiti", "KaiTi",
    "AR PL UKai CN", "AR PL UKai TW", "AR PL UKai HK", "AR PL KaitiM GB", "TW-Kai",
)
# 注释里的西文：先试细字重的比例字（macOS），取不到再退回代码等宽斜体。
# 顺序按"谁真的装了细字重"排：Linux 上 Noto Sans CJK SC Light 有 Light 字重，
# macOS 上没有这个族，会落到 Helvetica Neue Light Italic。
COMMENT_FONT = (
    '"Noto Sans CJK SC Light", "Helvetica Neue", "Avenir Next", '
    "$code_font"
)
CHROMIUM_BINS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome")
# macOS: Chrome/Chromium/Edge/Brave 在 app bundle 里，不在 PATH 上。
CHROMIUM_APP_BINS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
)
PAPERS = ("A4", "A3", "A5", "Letter", "Legal")
# 纸张尺寸（pt，宽 x 高）：屏幕预览要按它算正文宽度。
PAPER_PT = {
    "A3": (841.89, 1190.55),
    "A4": (595.276, 841.89),
    "A5": (419.53, 595.28),
    "Legal": (612.0, 1008.0),
    "Letter": (612.0, 792.0),
}
CSS_UNITS = {"mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0, "pt": 1.0, "px": 0.75}
SCRIPTS_DIR = Path(__file__).resolve().parent
CSS = Template(
    """
@page { size: $paper $orientation; margin: $margin; }
html, body { margin: 0; padding: 0; }
$theme_css
$cjk_face_css
body {
  font-family: $font;
  font-size: ${code_pt}pt;
  line-height: 1.5;
  color: var(--fg);
  background: var(--bg);
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

/* 屏幕预览：一张白纸 + 与打印一致的页边距（@page 只在打印时生效）。 */
@media screen {
  html, body { background: var(--page-bg); }
  body { padding: 10mm 0; }
  .page {
    width: ${content_w}pt;
    margin: 0 auto;
    padding: $pad;
    background: var(--bg);
    box-shadow: 0 .3mm 1.2mm var(--shadow);
  }
}
@media print {
  html, body { background: var(--bg); padding: 0; }
  .page { width: auto; margin: 0; padding: 0; background: var(--bg); box-shadow: none; }
}

.doc-head { border-bottom: 1.6pt solid var(--rule-strong); padding-bottom: 3mm; margin-bottom: 4mm; }
.doc-head h1 { font-size: ${h1_pt}pt; margin: 0 0 1.5mm 0; letter-spacing: .4pt; }
.doc-head .meta { font-size: ${meta_pt}pt; color: var(--muted); }
.sheet { break-before: page; page-break-before: always; }
.sheet:first-of-type { break-before: auto; page-break-before: auto; }
.file-head {
  border-bottom: 1pt solid var(--rule-strong);
  margin: 0 0 2.5mm 0;
  padding-bottom: 1.5mm;
  break-after: avoid;
  page-break-after: avoid;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 4mm;
}
.file-head .name { font-size: ${name_pt}pt; font-weight: bold; }
.file-head .sub { font-size: ${meta_pt}pt; color: var(--muted); white-space: nowrap; }
table.code { border-collapse: collapse; width: 100%; font-family: $code_font; }
table.code tr { break-inside: avoid; page-break-inside: avoid; }
table.code td { vertical-align: top; padding: 0; }
td.num {
  width: 9mm;
  text-align: right;
  padding-right: 2.5mm !important;
  color: var(--num-fg);
  font-size: ${num_pt}pt;
  border-right: .4pt solid var(--rule-soft);
}
td.line { padding-left: 2.5mm !important; }
td.line .ln { white-space: pre-wrap; overflow-wrap: anywhere; padding-left: 4mm; text-indent: -4mm; }
pre.plain { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; font-family: $code_font; font-size: ${code_pt}pt; }

/* Markdown 正文 */
.md-body { font-size: ${prose_pt}pt; line-height: 1.6; }
.md-body h1, .md-body h2, .md-body h3, .md-body h4, .md-body h5, .md-body h6 {
  break-after: avoid; page-break-after: avoid; margin: 4mm 0 1.5mm 0; line-height: 1.3;
}
.md-body h1 { font-size: ${h1_pt}pt; border-bottom: 1pt solid var(--rule-strong); padding-bottom: 1mm; }
.md-body h2 { font-size: ${h2_pt}pt; border-bottom: .5pt solid var(--rule-soft); padding-bottom: .8mm; }
.md-body h3 { font-size: ${h3_pt}pt; }
.md-body h4, .md-body h5, .md-body h6 { font-size: ${prose_pt}pt; }
.md-body p { margin: 0 0 2mm 0; }
.md-body ul, .md-body ol { margin: 0 0 2mm 0; padding-left: 6mm; }
.md-body li { margin: 0 0 .8mm 0; }
.md-body li > p { margin: 0; }
.md-body pre {
  background: var(--code-bg);
  border: .5pt solid var(--code-border);
  border-radius: 1mm;
  padding: 2mm 2.5mm;
  margin: 0 0 2.5mm 0;
  break-inside: avoid;
  page-break-inside: avoid;
}
.md-body pre code {
  font-family: $code_font;
  font-size: ${code_pt}pt;
  line-height: 1.45;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: none;
  padding: 0;
}
.md-body code { font-family: $code_font; font-size: ${code_pt}pt; background: var(--inline-bg); padding: 0 .6mm; }
.md-body blockquote {
  margin: 0 0 2.5mm 0;
  padding: 0 0 0 3mm;
  border-left: 1.5pt solid var(--rule-soft);
  color: var(--quote-fg);
}
.md-body table { border-collapse: collapse; width: 100%; margin: 0 0 2.5mm 0; font-size: ${code_pt}pt; }
.md-body th, .md-body td { border: .5pt solid var(--rule-soft); padding: 1mm 1.5mm; text-align: left; vertical-align: top; }
.md-body th { background: var(--th-bg); }
.md-body img { max-width: 100%; }
.md-body a { color: var(--link); }
.md-body hr { border: none; border-top: .5pt solid var(--rule-soft); margin: 3mm 0; }
.md-body input[type=checkbox] { margin-right: 1mm; }

$hl_css
"""
)

EXAMPLES = """\
示例：
  sheet.py README.md src/main.cpp src/counter.h
  sheet.py -o /tmp/sheet --title "day4 源码" counter.h counter.cpp main.cpp
  sheet.py notes.md --font 'Noto Serif CJK SC, serif' --font-size 9
  sheet.py --theme dark --html-only src/main.go       # 深色、只出 HTML
  sheet.py notes.md --theme auto                      # 屏幕跟系统深浅色，打印仍浅色
  sheet.py src/*.py --print                # 生成后送系统默认打印机
  sheet.py doc.md --print HP-M128fn        # 送指定队列
"""


# ---------------------------------------------------------------- 主题

THEMES = ("light", "dark", "auto")

# 主题调色板（CSS 变量）：light 用于打印（省墨、对比度高），dark 适合屏幕阅读，
# auto = 屏幕跟随系统深浅色、打印/PDF 仍是 light。
THEME_VARS = {
    "light": {
        "bg": "#ffffff",
        "fg": "#1a1a1a",
        "muted": "#5b6169",
        "rule-strong": "#111111",
        "rule-soft": "#b9bdc3",
        "num-fg": "#8a9098",
        "code-bg": "#f6f7f8",
        "code-border": "#d7dade",
        "inline-bg": "#eef0f2",
        "th-bg": "#f0f1f3",
        "quote-fg": "#3d434a",
        "page-bg": "#e8e8e8",
        "shadow": "rgba(0, 0, 0, .18)",
        "link": "#1155cc",
    },
    "dark": {
        "bg": "#1b1d21",
        "fg": "#e3e6ea",
        "muted": "#9aa4b0",
        "rule-strong": "#dfe3e8",
        "rule-soft": "#3a4149",
        "num-fg": "#7d8794",
        "code-bg": "#15171a",
        "code-border": "#333a42",
        "inline-bg": "#2a2f36",
        "th-bg": "#24282e",
        "quote-fg": "#b6bec8",
        "page-bg": "#0f1114",
        "shadow": "rgba(0, 0, 0, .6)",
        "link": "#7cb7ff",
    },
}

# 语法高亮的 token 配色，类名见下面的高亮器（k/kt/nb/nf/nc/s/c/m/o/a）。
TOKEN_COLORS = {
    "light": {
        "k": "#cf222e",
        "kt": "#953800",
        "nb": "#6639ba",
        "nf": "#8250df",
        "nc": "#953800",
        "s": "#0a3069",
        "c": "#6e7781",
        "m": "#0550ae",
        "o": "#b35900",
        "a": "#0969da",
    },
    "dark": {
        "k": "#ff7b72",
        "kt": "#ffa657",
        "nb": "#d2a8ff",
        "nf": "#d2a8ff",
        "nc": "#ffa657",
        "s": "#a5d6ff",
        "c": "#8b949e",
        "m": "#79c0ff",
        "o": "#ffa657",
        "a": "#79c0ff",
    },
}


def _vars_block(theme: str) -> str:
    body = "".join("\n  --{}: {};".format(key, value) for key, value in THEME_VARS[theme].items())
    return ":root {{{}\n}}".format(body)


def theme_css(theme: str) -> str:
    if theme == "dark":
        return _vars_block("dark")
    if theme == "auto":
        return (
            _vars_block("light")
            + "\n@media screen and (prefers-color-scheme: dark) {\n"
            + _vars_block("dark")
            + "\n}\n"
        )
    return _vars_block("light")


def cjk_families(value: str) -> list[str]:
    """--cjk-font 的值 -> [族名]；none/off 或空表示不用楷体。"""
    text = (value or "").strip()
    if text.lower() in ("none", "off", "-"):
        return []
    return [name.strip().strip('"').strip("'") for name in text.split(",") if name.strip()]


def cjk_face_css(families: list[str]) -> str:
    """只接管 CJK 码位的 @font-face：汉字走楷体，西文/数字仍按等宽字体走。

    一个楷体族名都取不到时这条 @font-face 整体失效，中文回落原字体
    （不会变方块），也就是"代码汉字楷体"这个效果自动消失。
    """
    if not families:
        return "/* --cjk-font none：代码区汉字不做楷体替换 */"
    source = ", ".join('local("{}")'.format(name) for name in families)
    return (
        "@font-face {\n"
        '  font-family: "SheetCJK";\n'
        "  src: " + source + ";\n"
        "  unicode-range: U+2E80-2EFF, U+2F00-2FDF, U+3000-303F, U+31C0-31EF,\n"
        "                 U+3200-4DBF, U+4E00-9FFF, U+F900-FAFF, U+FE30-FE4F,\n"
        "                 U+FF00-FF60, U+FFE0-FFE6, U+20000-2FA1F;\n"
        "}\n"
    )


def cjk_font_name(families: list[str]) -> str | None:
    """本机实际能命中的楷体族名（没有装楷体时返回 None）。"""
    if not shutil.which("fc-match"):
        return None
    for name in families:
        try:
            proc = subprocess.run(
                ["fc-match", "-f", "%{family}", name],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        family = (proc.stdout or "").split(",")[0].strip()
        if family.lower() == name.lower():
            return name
    return None


def _token_rules(palette: dict, comment_font: str) -> str:
    rules = []
    for key in ("k", "kt", "nb", "nf", "nc", "s", "m", "o", "a"):
        color = palette.get(key)
        if color:
            rules.append(".hl .{} {{ color: {}; }}".format(key, color))
    # 注释整体继承代码字体（汉字照旧走 SheetCJK → 楷体），
    # 只有被 <i> 包起来的西文才换成细字重斜体。
    rules.append(".hl .c {{ color: {}; }}".format(palette["c"]))
    rules.append(
        ".hl .c i {{ font-family: {}; font-style: italic; font-weight: 300; }}".format(
            comment_font
        )
    )
    return "\n".join(rules)


def highlight_css(theme: str, comment_font: str) -> str:
    if theme == "dark":
        return _token_rules(TOKEN_COLORS["dark"], comment_font)
    if theme == "auto":
        return (
            _token_rules(TOKEN_COLORS["light"], comment_font)
            + "\n@media screen and (prefers-color-scheme: dark) {\n"
            + _token_rules(TOKEN_COLORS["dark"], comment_font)
            + "\n}\n"
        )
    return _token_rules(TOKEN_COLORS["light"], comment_font)


# ---------------------------------------------------------------- 代码高亮
#
# 不依赖 Pygments：按语言语法扫一遍源码，把 token 分成几类再交给 CSS 上色。
#   k  关键字      kt 类型/字面量   nb 内建   nf 函数名/调用   nc 类名/组件名
#   s  字符串      c  注释          m  数字   o  运算符        a  属性名/键名
# 认不出的语言退回纯文本（不高亮总比高亮错好）。

_NUMBER_PATTERN = (
    r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|"
    r"\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?[fFuUlL]*|"
    r"\.[\d_]+[fF]?"
)

_SPECS: dict[str, dict] = {}
_EXT_LANG: dict[str, str] = {}
_LANG_ALIASES: dict[str, str] = {}


def _register(
    lang: str,
    exts: tuple = (),
    aliases: tuple = (),
    keywords: tuple = (),
    types: tuple = (),
    builtins: tuple = (),
    line: tuple = (),
    block: tuple = (),
    strings: tuple = (),
    ident: str = r"[A-Za-z_][A-Za-z0-9_]*",
    fold: bool = False,
    classes: bool = False,
    keysep: tuple = (),
    keystr: bool = False,
) -> None:
    def norm(words: tuple) -> set:
        return {word.lower() if fold else word for word in words}

    _SPECS[lang] = {
        "kw": norm(keywords),
        "ty": norm(types),
        "bi": norm(builtins),
        "line": tuple(line),
        "block": tuple(block),
        "strings": tuple(strings) or (('"', '"', False), ("'", "'", False)),
        "ident": ident,
        "fold": fold,
        "classes": classes,
        "keysep": tuple(keysep),
        "keystr": keystr,
    }
    for ext in exts:
        _EXT_LANG[ext.lower().lstrip(".")] = lang
    _LANG_ALIASES[lang] = lang
    for alias in aliases:
        _LANG_ALIASES[alias.lower()] = lang


_C_KEYWORDS = """auto break case const continue default do else enum extern for goto if
inline register restrict return sizeof static struct switch typedef union volatile while
class namespace template typename this nullptr operator private protected public virtual
friend using new delete try catch throw constexpr noexcept override final""".split()
_C_TYPES = """void char short int long float double signed unsigned bool wchar_t
size_t ssize_t ptrdiff_t uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t
FILE va_list string vector map unordered_map set pair tuple""".split()
_C_BUILTINS = """printf fprintf sprintf snprintf scanf malloc calloc realloc free memcpy
memset memmove strlen strcmp strcpy std cout cin cerr endl NULL true false""".split()

_GO_KEYWORDS = """break case chan const continue default defer else fallthrough for func go
goto if import interface map package range return select struct switch type var""".split()
_GO_TYPES = """bool byte complex64 complex128 error float32 float64 int int8 int16 int32
int64 rune string uint uint8 uint16 uint32 uint64 uintptr any comparable""".split()
_GO_BUILTINS = """append cap clear close complex copy delete imag len make max min new panic
print println real recover nil true false iota""".split()

_JS_KEYWORDS = """await async break case catch class const continue debugger default delete
do else export extends finally for function if import in instanceof let new of return
static super switch this throw try typeof var void while with yield get set""".split()
_JS_TYPES = "true false null undefined NaN Infinity".split()
_JS_BUILTINS = """console window document globalThis Object Array String Number Boolean Math
JSON Promise Map Set Symbol Date RegExp Error setTimeout setInterval require module exports
process print parseInt parseFloat isNaN""".split()

_PY_KEYWORDS = """and as assert async await break class continue def del elif else except
finally for from global if import in is lambda nonlocal not or pass raise return try while
with yield match case""".split()
_PY_TYPES = "True False None NotImplemented Ellipsis self cls".split()
_PY_BUILTINS = """abs all any ascii bin bool bytearray bytes callable chr classmethod compile
complex delattr dict dir divmod enumerate eval exec filter float format frozenset getattr
globals hasattr hash help hex id input int isinstance issubclass iter len list locals map max
memoryview min next object oct open ord pow print property range repr reversed round set
setattr slice sorted staticmethod str sum super tuple type vars zip""".split()

_QML_EXTRA = "property signal readonly default required component on pragma alias enum id".split()

_RUST_KEYWORDS = """as async await break const continue crate dyn else enum extern fn for if
impl in let loop match mod move mut pub ref return self Self static struct super trait type
unsafe use where while""".split()
_RUST_TYPES = """bool char f32 f64 i8 i16 i32 i64 i128 isize str u8 u16 u32 u64 u128 usize
String Vec Option Result Box Rc Arc RefCell HashMap""".split()
_RUST_BUILTINS = """println print format vec panic assert assert_eq unimplemented todo Some
None Ok Err true false""".split()

_JVM_KEYWORDS = """abstract assert boolean break byte case catch char class const continue
default do double else enum extends final finally float for fun goto if implements import
in instanceof int interface long native new package private protected public return sealed
short static strictfp super switch synchronized this throw throws trait transient try var
void volatile while yield object override open data when typealias suspend companion init
constructor where is as out by lazy lateinit internal""".split()
_JVM_TYPES = """String Int Long Double Float Boolean Char Byte Short Any Unit Nothing List
Map Set Array ArrayList HashMap HashSet Optional Integer Character true false null None""".split()
_JVM_BUILTINS = """System out println print equals toString super this console""".split()

_SH_KEYWORDS = """if then else elif fi for while until do done case esac in function select
time return break continue local export readonly declare typeset coproc""".split()
_SH_BUILTINS = """echo printf read cd pwd ls cp mv rm mkdir rmdir touch cat grep sed awk cut
sort uniq head tail wc find xargs chmod chown df du ps kill sleep source exit set unset shift
test true false getopts trap eval exec""".split()

_LUA_KEYWORDS = """and break do else elseif end false for function goto if in local nil not
or repeat return then true until while""".split()
_LUA_BUILTINS = "print pairs ipairs type tostring tonumber error assert pcall require".split()

_SQL_KEYWORDS = """select from where group by having order limit offset insert into values
update set delete create table index view drop alter add primary key foreign references
join inner left right full outer union all distinct as and or not null is like ilike between
exists case when then else end begin commit rollback grant revoke with returning""".split()
_SQL_TYPES = """int integer bigint smallint tinyint serial text varchar char boolean date
timestamp timestamptz time numeric decimal real float double json jsonb uuid bytea""".split()
_SQL_BUILTINS = """count sum avg min max coalesce cast now current_date current_timestamp
nullif greatest least""".split()

_register("c", ("c",), aliases=("h",), keywords=_C_KEYWORDS, types=_C_TYPES,
          builtins=_C_BUILTINS, line=("//",), block=(("/*", "*/"),), classes=True)
_register("cpp", ("cpp", "cc", "cxx", "hpp", "hh", "inl", "ipp", "proto"),
          aliases=("c++", "cxx"), keywords=_C_KEYWORDS, types=_C_TYPES, builtins=_C_BUILTINS,
          line=("//",), block=(("/*", "*/"),), classes=True)
_register("go", ("go",), keywords=_GO_KEYWORDS, types=_GO_TYPES, builtins=_GO_BUILTINS,
          line=("//",), block=(("/*", "*/"),),
          strings=(('"', '"', False), ("'", "'", False), ("`", "`", True)), classes=True)
_register("python", ("py", "pyi", "pyw", "pyx"), aliases=("python3", "py3"),
          keywords=_PY_KEYWORDS, types=_PY_TYPES, builtins=_PY_BUILTINS, line=("#",),
          strings=(('"""', '"""', True), ("'''", "'''", True), ('"', '"', False), ("'", "'", False)),
          classes=True)
_register("javascript", ("js", "mjs", "cjs", "jsx"),
          aliases=("js", "node", "jsx", "ecmascript"), keywords=_JS_KEYWORDS, types=_JS_TYPES,
          builtins=_JS_BUILTINS, line=("//",), block=(("/*", "*/"),),
          strings=(('"', '"', False), ("'", "'", False), ("`", "`", True)),
          ident=r"[A-Za-z_$][A-Za-z0-9_$]*", classes=True, keysep=(":",))
_register("typescript", ("ts", "tsx", "mts", "cts"), aliases=("ts", "tsx"),
          keywords=_JS_KEYWORDS + "interface type declare readonly namespace abstract implements enum private public protected as satisfies keyof infer is asserts".split(),
          types=_JS_TYPES + "string number boolean any unknown never object symbol bigint".split(),
          builtins=_JS_BUILTINS, line=("//",), block=(("/*", "*/"),),
          strings=(('"', '"', False), ("'", "'", False), ("`", "`", True)),
          ident=r"[A-Za-z_$][A-Za-z0-9_$]*", classes=True, keysep=(":",))
_register("qml", ("qml", "qmltypes"), keywords=_JS_KEYWORDS + _QML_EXTRA,
          types=_JS_TYPES + "int real double string bool url var list color font date time point size rect matrix quaternion vector2d vector3d vector4d".split(),
          builtins=_JS_BUILTINS + "Qt Component".split(), line=("//",), block=(("/*", "*/"),),
          strings=(('"', '"', False), ("'", "'", False), ("`", "`", True)),
          ident=r"[A-Za-z_$][A-Za-z0-9_$]*", classes=True, keysep=(":",))
_register("rust", ("rs",), keywords=_RUST_KEYWORDS, types=_RUST_TYPES,
          builtins=_RUST_BUILTINS, line=("//",), block=(("/*", "*/"),), classes=True)
_register("java", ("java",), aliases=("jvm",), keywords=_JVM_KEYWORDS, types=_JVM_TYPES,
          builtins=_JVM_BUILTINS, line=("//",), block=(("/*", "*/"),), classes=True)
_register("kotlin", ("kt", "kts"), keywords=_JVM_KEYWORDS, types=_JVM_TYPES,
          builtins=_JVM_BUILTINS, line=("//",), block=(("/*", "*/"),), classes=True)
_register("csharp", ("cs",), aliases=("c#",), keywords=_JVM_KEYWORDS, types=_JVM_TYPES,
          builtins=_JVM_BUILTINS, line=("//",), block=(("/*", "*/"),), classes=True)
_register("swift", ("swift",), keywords=_JVM_KEYWORDS, types=_JVM_TYPES,
          builtins=_JVM_BUILTINS, line=("//",), block=(("/*", "*/"),), classes=True)
_register("shell", ("sh", "bash", "zsh", "ksh", "fish"),
          aliases=("bash", "sh", "zsh", "shell-session"),
          keywords=_SH_KEYWORDS, builtins=_SH_BUILTINS, line=("#",),
          strings=(('"', '"', False), ("'", "'", False), ("`", "`", True)),
          ident=r"[A-Za-z_$][A-Za-z0-9_]*", classes=False)
_register("lua", ("lua",), keywords=_LUA_KEYWORDS, builtins=_LUA_BUILTINS, line=("--",),
          block=(("--[[", "]]"),), strings=(('"', '"', False), ("'", "'", False)))
_register("sql", ("sql",), keywords=_SQL_KEYWORDS, types=_SQL_TYPES, builtins=_SQL_BUILTINS,
          line=("--",), block=(("/*", "*/"),), fold=True)
_register("json", ("json", "jsonc", "json5", "geojson"), aliases=("json",),
          keywords=(), types="true false null".split(), line=(),
          strings=(('"', '"', False),), keysep=(":",), keystr=True)
_register("yaml", ("yaml", "yml"), aliases=("yml",), line=("#",),
          types="true false null yes no on off".split(), keysep=(":",), keystr=True)
_register("toml", ("toml",), line=("#",), keysep=(":", "="), keystr=True,
          types="true false".split())
_register("ini", ("ini", "cfg", "conf", "properties"), line=("#", ";"),
          keysep=(":", "="), keystr=True, types="true false yes no on off".split())
_register("make", ("mk", "mak", "cmake"), aliases=("makefile", "make"), line=("#",),
          keywords=("include export define endef ifeq ifneq ifdef ifndef else endif override "
                    "vpath undefine private").split(),
          ident=r"[A-Za-z_][A-Za-z0-9_.-]*")
_register("docker", ("dockerfile",), aliases=("dockerfile",), fold=True, line=("#",),
          keywords=("from run cmd label maintainer expose env add copy entrypoint volume "
                    "user workdir arg onbuild stopsignal healthcheck shell").split())
_register("css", ("css", "scss", "less"), aliases=("css", "scss", "less"), line=("//",),
          block=(("/*", "*/"),), ident=r"-?[A-Za-z_][A-Za-z0-9_-]*", keysep=(":",))
# 只做注释识别、不做关键字高亮的语言（注释样式与高亮共用同一套 spec）
_register("asm", ("s", "asm", "inc", "nasm"), aliases=("asm", "nasm"), line=(";",))
_register("tex", ("tex", "sty", "cls", "ltx"), aliases=("tex", "latex", "latex2e"),
          line=("%",))
_register("vim", ("vim",), aliases=("vim",), line=('"',))
_register("lisp", ("lisp", "lsp", "scm", "clj", "cljs", "rkt", "el"),
          aliases=("lisp", "clojure", "scheme", "elisp"), line=(";",))
# 认不出语言的源码按 generic 处理：`//`、`#` 当行注释，`/* */` 当块注释。
_register("generic", aliases=("generic",), line=("//", "#"),
          block=(("/*", "*/"),))
# 这些扩展名是纯文本/散文，不做注释样式（保持原样）
PLAIN_EXTS = {"txt", "log", "csv", "tsv", "rst", "org", "adoc"}

for _html_ext in ("html", "htm", "xhtml", "vue", "svelte", "xml", "svg", "xsl", "xslt",
                  "plist", "ui", "qrc", "resx"):
    _EXT_LANG[_html_ext] = "html"
_LANG_ALIASES.update({"html": "html", "htm": "html", "xml": "html", "vue": "html",
                      "svelte": "html", "svg": "html", "markup": "html"})

_HTML_RE = re.compile(
    r"(?P<c><!--[\s\S]*?(?:-->|$))"
    r"|(?P<k><![^>]*>)"
    r"|(?P<tag></?[A-Za-z][\w:.\-]*)"
    r"|(?P<a>[A-Za-z_:][\w:.\-]*)(?=\s*=)"
    r"|(?P<s>\"[^\"]*\"|'[^']*')"
    r"|(?P<o></?>|=)",
)


def normalize_lang(name: str | None) -> str | None:
    """把扩展名/别名（go、c++、bash…）归一到内部语言名。"""
    if not name:
        return None
    return _LANG_ALIASES.get(name.strip().lower())


def lang_for_path(path: Path | None) -> str | None:
    if path is None:
        return None
    name = path.name.lower()
    if name in ("makefile", "gnumakefile", "makefile.am"):
        return "make"
    if name.startswith("dockerfile"):
        return "docker"
    return _EXT_LANG.get(path.suffix.lower().lstrip("."))


def supported_langs() -> str:
    return ", ".join(sorted(set(_SPECS) | {"html"}))


def code_lang(path: Path | None, forced: str | None = None) -> str | None:
    """代码区语言：--lang > 扩展名 > generic（纯文本扩展名除外）。"""
    if forced:
        return forced
    if path is None:
        return None
    lang = lang_for_path(path)
    if lang:
        return lang
    if path.suffix.lower().lstrip(".") in PLAIN_EXTS:
        return None
    return "generic"


def _push(lines: list, cls: str, value: str) -> None:
    for index, part in enumerate(value.split("\n")):
        if index:
            lines.append([])
        if part:
            lines[-1].append((cls, part))


_PATTERNS: dict[str, object] = {}


def _build_pattern(spec: dict):
    alternatives = []
    comments = []
    for start, end in spec["block"]:
        comments.append(re.escape(start) + r"[\s\S]*?(?:" + re.escape(end) + r"|$)")
    for marker in spec["line"]:
        comments.append(re.escape(marker) + r"[^\n]*")
    if comments:
        alternatives.append("(?P<c>" + "|".join(comments) + ")")
    strings = []
    for open_, close_, multiline in spec["strings"]:
        start, end = re.escape(open_), re.escape(close_)
        if multiline:
            strings.append(start + r"[\s\S]*?(?:" + end + r"|$)")
        else:
            strings.append(
                start + r"(?:[^" + re.escape(open_[0]) + r"\\\n]|\\.)*" + end + r"?"
            )
    if strings:
        alternatives.append("(?P<s>" + "|".join(strings) + ")")
    alternatives.append("(?P<m>" + _NUMBER_PATTERN + ")")
    alternatives.append("(?P<n>" + spec["ident"] + ")")
    alternatives.append(r"(?P<o>[-+*/%=<>!&|^~?:@]+)")
    alternatives.append(r"(?P<p>[{}\[\]();,.])")
    return re.compile("|".join(alternatives))


def _classify(lines: list, spec: dict) -> None:
    keysep = spec["keysep"]
    for line in lines:
        for index, (cls, text) in enumerate(line):
            if cls not in ("n", "s"):
                continue
            nxt = line[index + 1][1] if index + 1 < len(line) else ""
            is_key = bool(keysep) and any(nxt.startswith(sep) for sep in keysep)
            if cls == "s":
                if is_key and spec["keystr"]:
                    line[index] = ("a", text)
                continue
            key = text.lower() if spec["fold"] else text
            if key in spec["kw"]:
                line[index] = ("k", text)
            elif key in spec["ty"]:
                line[index] = ("kt", text)
            elif key in spec["bi"]:
                line[index] = ("nb", text)
            elif is_key:
                line[index] = ("a", text)
            elif nxt.startswith("("):
                line[index] = ("nf", text)
            elif spec["classes"] and text[:1].isupper() and text[1:2].islower():
                line[index] = ("nc", text)


def highlight_lines(text: str, lang: str | None) -> list[list[tuple[str, str]]] | None:
    """把源码切成按行分组的 (类别, 文本)；语言不支持时返回 None（纯文本）。"""
    if not lang:
        return None
    if lang == "html":
        html_lines: list[list[tuple[str, str]]] = [[]]
        pos = 0
        for match in _HTML_RE.finditer(text):
            if match.start() > pos:
                _push(html_lines, "", text[pos:match.start()])
            kind = match.lastgroup or ""
            cls = {"c": "c", "k": "k", "tag": "k", "a": "a", "s": "s", "o": "o"}.get(kind, "")
            _push(html_lines, cls, match.group())
            pos = match.end()
        if pos < len(text):
            _push(html_lines, "", text[pos:])
        return html_lines
    spec = _SPECS.get(lang)
    if spec is None:
        return None
    pattern = _PATTERNS.get(lang)
    if pattern is None:
        pattern = _PATTERNS[lang] = _build_pattern(spec)
    lines: list[list[tuple[str, str]]] = [[]]
    pos = 0
    for match in pattern.finditer(text):
        if match.start() > pos:
            _push(lines, "", text[pos:match.start()])
        kind = match.lastgroup or ""
        _push(lines, "" if kind == "p" else kind, match.group())
        pos = match.end()
    if pos < len(text):
        _push(lines, "", text[pos:])
    _classify(lines, spec)
    return lines


# ---------------------------------------------------------------- 输入处理


def read_text(path: Path) -> str | None:
    """读取文本文件；遇到二进制内容返回 None。"""
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def inline(text: str) -> str:
    """内联 Markdown 的降级实现（仅在 md2html 不可用时使用）。"""
    text = html.escape(text, quote=False)
    spans: list[str] = []

    def stash(match: re.Match) -> str:
        spans.append(match.group(1))
        return "\x00{}\x00".format(len(spans) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    return re.sub(r"\x00(\d+)\x00", lambda m: "<code>{}</code>".format(spans[int(m.group(1))]), text)


def builtin_markdown(text: str) -> str:
    """最小可用的 Markdown 渲染器（标题/列表/表格/引用/代码块），作为 md2html 的兜底。"""
    lines = text.split("\n")
    out: list[str] = []
    para: list[str] = []
    quote: list[str] = []
    items: list[list[str]] = []
    ordered = False
    fence: list[str] | None = None
    lang = ""
    index = 0

    def flush_para() -> None:
        if para:
            out.append("<p>{}</p>".format(inline(" ".join(para).strip())))
            para.clear()

    def flush_quote() -> None:
        if quote:
            out.append("<blockquote>{}</blockquote>".format(inline(" ".join(quote).strip())))
            quote.clear()

    def flush_list() -> None:
        if items:
            tag = "ol" if ordered else "ul"
            body = "".join("<li>{}</li>".format(inline(" ".join(i).strip())) for i in items)
            out.append("<{0}>{1}</{0}>".format(tag, body))
            items.clear()

    def flush_all() -> None:
        flush_para()
        flush_quote()
        flush_list()

    def cells(row: str) -> list[str]:
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    while index < len(lines):
        line = lines[index]
        index += 1
        if fence is not None:
            if line.strip().startswith("```"):
                out.append(
                    '<pre><code class="language-{}">{}</code></pre>'.format(
                        lang, html.escape("\n".join(fence))
                    )
                )
                fence = None
                lang = ""
            else:
                fence.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_all()
            fence = []
            lang = stripped[3:].strip()
        elif not stripped:
            flush_all()
        elif (
            stripped.startswith("|")
            and index < len(lines)
            and re.match(r"^\|?[\s:|-]+\|[\s:|-]*$", lines[index].strip())
        ):
            flush_all()
            header = cells(stripped)
            index += 1  # 跳过分隔行
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(cells(lines[index].strip()))
                index += 1
            out.append(
                "<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>".format(
                    "".join("<th>{}</th>".format(inline(c)) for c in header),
                    "".join(
                        "<tr>{}</tr>".format(
                            "".join("<td>{}</td>".format(inline(c)) for c in row)
                        )
                        for row in rows
                    ),
                )
            )
        elif re.match(r"^#{1,6}\s", stripped):
            flush_all()
            level = len(stripped) - len(stripped.lstrip("#"))
            out.append("<h{0}>{1}</h{0}>".format(level, inline(stripped[level:].strip())))
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_all()
            out.append("<hr>")
        elif stripped.startswith(">"):
            flush_para()
            flush_list()
            quote.append(stripped[1:].strip())
        elif re.match(r"^[-*+]\s+", stripped):
            flush_para()
            flush_quote()
            if ordered:
                flush_list()
            ordered = False
            items.append([re.sub(r"^[-*+]\s+", "", stripped)])
        elif re.match(r"^\d+[.)]\s+", stripped):
            flush_para()
            flush_quote()
            if not ordered:
                flush_list()
            ordered = True
            items.append([re.sub(r"^\d+[.)]\s+", "", stripped)])
        else:
            if items:
                items[-1].append(stripped)
            elif quote:
                quote.append(stripped)
            else:
                para.append(stripped)
    if fence is not None:
        out.append("<pre><code>{}</code></pre>".format(html.escape("\n".join(fence))))
    flush_all()
    return "\n".join(out)


def render_markdown(path: Path | None, text: str, mode: str) -> str:
    if mode in ("auto", "md2html") and shutil.which("md2html"):
        if path is not None:
            proc = subprocess.run(
                ["md2html", "--github", str(path)], capture_output=True, text=True, timeout=60
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
        proc = subprocess.run(
            ["md2html", "--github"], input=text, capture_output=True, text=True, timeout=60
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    return builtin_markdown(text)


# ---------------------------------------------------------------- HTML 组装

FENCE_RE = re.compile(r"<pre><code([^>]*)>(.*?)</code></pre>", re.S)
FENCE_LANG_RE = re.compile(r"""language-([A-Za-z0-9_+#.-]+)""")
# 中日韩文字 + 全角标点：这些字符单独包一层，好让它们走楷体、不跟着斜体。
CJK_RE = re.compile(
    r"[\u1100-\u11ff\u2e80-\u2eff\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]+"
)


def style_comment_text(text: str) -> str:
    """注释正文：汉字段原样（楷体由代码字体负责），西文段包 <i> 走细斜体。"""
    chunks: list[str] = []
    cursor = 0
    for match in CJK_RE.finditer(text):
        if match.start() > cursor:
            chunks.append("<i>{}</i>".format(html.escape(text[cursor:match.start()])))
        chunks.append(html.escape(match.group()))
        cursor = match.end()
    if cursor < len(text):
        chunks.append("<i>{}</i>".format(html.escape(text[cursor:])))
    return "".join(chunks)


def _render_tokens(tokens: list[tuple[str, str]], style_comments: bool = True) -> str:
    """把一行 token 渲染成 HTML；空行返回空串，交给调用方补位。"""
    chunks: list[str] = []
    for cls, part in tokens:
        if cls == "c":
            inner = style_comment_text(part) if style_comments else html.escape(part)
            chunks.append('<span class="c">{}</span>'.format(inner))
        elif cls:
            chunks.append('<span class="{}">{}</span>'.format(cls, html.escape(part)))
        else:
            chunks.append(html.escape(part))
    return "".join(chunks)


def _render_highlighted(lines: list[list[tuple[str, str]]], style_comments: bool = True) -> str:
    return "\n".join(_render_tokens(line, style_comments) for line in lines)


def highlight_md_code(rendered: str, style_comments: bool = True) -> str:
    """给 Markdown 的围栏代码块加高亮；认不出的语言按 generic 只做注释样式。"""

    def repl(match: re.Match) -> str:
        attrs, inner = match.group(1), match.group(2)
        found = FENCE_LANG_RE.search(attrs)
        lang = normalize_lang(found.group(1)) if found else None
        lines = highlight_lines(html.unescape(inner), lang or "generic")
        if lines is None:
            return match.group(0)
        return '<pre class="hl"><code{}>{}</code></pre>'.format(
            attrs, _render_highlighted(lines, style_comments)
        )

    return FENCE_RE.sub(repl, rendered)


def code_section(
    label: str,
    text: str,
    line_numbers: bool,
    lang: str | None = None,
    style_comments: bool = True,
) -> str:
    highlighted = highlight_lines(text, lang)
    source_lines = text.splitlines()
    rows = []
    for number, raw in enumerate(source_lines, start=1):
        if highlighted is not None and number - 1 < len(highlighted):
            cell = _render_tokens(highlighted[number - 1], style_comments) or "&nbsp;"
        else:
            cell = html.escape(raw) or "&nbsp;"
        prefix = '<td class="num">{}</td>'.format(number) if line_numbers else ""
        rows.append(
            '<tr>{}<td class="line"><div class="{}">{}</div></td></tr>'.format(
                prefix, "ln hl" if highlighted is not None else "ln", cell
            )
        )
    if line_numbers:
        body = '<table class="code">{}</table>'.format("".join(rows))
    elif highlighted is not None:
        body = '<pre class="plain hl">{}</pre>'.format(
            _render_highlighted(highlighted, style_comments)
        )
    else:
        body = '<pre class="plain">{}</pre>'.format(html.escape(text))
    head = (
        '<div class="file-head"><span class="name">{}</span>'
        '<span class="sub">{} 行</span></div>'.format(html.escape(label), len(source_lines))
    )
    return '<section class="sheet">{}{}</section>'.format(head, body)


def markdown_section(label: str, rendered: str, highlight: bool, style_comments: bool = True) -> str:
    if highlight:
        rendered = highlight_md_code(rendered, style_comments)
    head = (
        '<div class="file-head"><span class="name">{}</span>'
        '<span class="sub">Markdown</span></div>'.format(html.escape(label))
    )
    return '<section class="sheet">{}<div class="md-body">{}</div></section>'.format(head, rendered)


def build_html(sources: list[tuple[str, Path | None, str, bool]], opts) -> str:
    code_pt = opts.font_size
    paper_w, paper_h = PAPER_PT[opts.paper]
    if opts.landscape:
        paper_w, paper_h = paper_h, paper_w
    pad_top, pad_right, pad_bottom, pad_left = parse_margin(opts.margin)
    content_w = max(paper_w - pad_left - pad_right, 72.0)
    families = cjk_families(opts.cjk_font)
    code_font = '"SheetCJK", {}'.format(opts.font) if families else opts.font
    comment_font = opts.comment_font.replace("$code_font", code_font)
    values = dict(
        paper=opts.paper,
        orientation="landscape" if opts.landscape else "portrait",
        margin=opts.margin,
        font=opts.font,
        code_font=code_font,
        content_w="{:.2f}".format(content_w),
        pad="{:.2f}pt {:.2f}pt {:.2f}pt {:.2f}pt".format(
            pad_top, pad_right, pad_bottom, pad_left
        ),
        theme_css=theme_css(opts.theme),
        cjk_face_css=cjk_face_css(families),
        hl_css=highlight_css(opts.theme, comment_font),
        code_pt=code_pt,
        prose_pt=code_pt + 2.0,
        num_pt=code_pt - 0.6,
        meta_pt=code_pt,
        name_pt=code_pt + 3.0,
        h1_pt=code_pt + 5.5,
        h2_pt=code_pt + 3.5,
        h3_pt=code_pt + 2.0,
    )
    sections = []
    forced_lang = normalize_lang(opts.lang)
    for label, path, text, is_markdown in sources:
        if is_markdown:
            sections.append(
                markdown_section(
                    label,
                    render_markdown(path, text, opts.md_renderer),
                    not opts.no_highlight,
                    not opts.no_comment_style,
                )
            )
        else:
            lang = None if opts.no_highlight else code_lang(path, forced_lang)
            sections.append(
                code_section(
                    label,
                    text,
                    not opts.no_line_numbers,
                    lang,
                    not opts.no_comment_style,
                )
            )
    total_lines = sum(len(text.splitlines()) for _, _, text, _ in sources)
    meta = "{} 个文件 · {} 行 · 生成于 {} · 行号对应源文件实际行号".format(
        len(sources), total_lines, dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    return (
        "<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>\n"
        "<title>{}</title>\n<style>{}</style></head><body>\n"
        "<div class='page'>\n"
        "<header class='doc-head'><h1>{}</h1><div class='meta'>{}</div></header>\n"
        "{}\n</div>\n</body></html>\n"
    ).format(
        html.escape(opts.title),
        CSS.substitute(values),
        html.escape(opts.title),
        html.escape(meta),
        "\n".join(sections),
    )


# ---------------------------------------------------------------- 渲染/预览


def parse_margin(text: str) -> tuple[float, float, float, float]:
    """CSS margin shorthand -> (top, right, bottom, left) in points."""
    values: list[float] = []
    for token in text.replace(",", " ").split():
        match = re.match(r"^([0-9]*\.?[0-9]+)(mm|cm|in|pt|px)?$", token.strip())
        if not match:
            continue
        unit = match.group(2) or "px"
        values.append(float(match.group(1)) * CSS_UNITS[unit])
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0], values[0], values[0])
    if len(values) == 2:
        return (values[0], values[1], values[0], values[1])
    if len(values) == 3:
        return (values[0], values[1], values[2], values[1])
    return (values[0], values[1], values[2], values[3])


def chromium_binary() -> str | None:
    for name in CHROMIUM_BINS:
        found = shutil.which(name)
        if found:
            return found
    for path in CHROMIUM_APP_BINS:
        if os.path.exists(path):
            return path
    return None


def python_with_fitz() -> str | None:
    """找一个能 import fitz（PyMuPDF）的解释器，做分页和预览。"""
    candidates = [
        sys.executable,
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
        shutil.which("python3"),
        "/opt/homebrew/bin/python3",
        "/usr/bin/python3",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            probe = subprocess.run(
                [candidate, "-c", "import fitz"], capture_output=True, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def render_webkit(
    html_path: Path,
    pdf_path: Path,
    paper: str,
    landscape: bool,
    margin: str,
    timeout: int,
) -> str:
    """macOS WebKit 渲染：整篇连续一页，再由 pdfsheet.py 切成纸张页。"""
    osascript = shutil.which("osascript")
    if not osascript:
        raise RuntimeError("这是 macOS 专有渲染器，找不到 osascript")
    python = python_with_fitz()
    if not python:
        raise RuntimeError("分页/预览需要 PyMuPDF（pip install pymupdf）")

    paper_w, paper_h = PAPER_PT[paper]
    if landscape:
        paper_w, paper_h = paper_h, paper_w
    top, right, bottom, left = parse_margin(margin)
    box_w = max(paper_w - left - right, 72.0)

    raw_pdf = pdf_path.with_name(pdf_path.stem + ".full.pdf")
    proc = subprocess.run(
        [
            osascript,
            "-l",
            "JavaScript",
            str(SCRIPTS_DIR / "wk2pdf.js"),
            str(html_path),
            str(raw_pdf),
            "{:.2f}".format(box_w),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    payload = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            payload = json.loads(line)
            break
        except ValueError:
            continue
    if not payload or not payload.get("ok"):
        detail = (payload or {}).get("error") or (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(
            "{}（沙箱里 WebKit 起不来时，用沙箱外权限重跑）".format(detail or "WebKit 未产出 PDF")
        )

    cut = subprocess.run(
        [
            python,
            str(SCRIPTS_DIR / "pdfsheet.py"),
            "paginate",
            "--in",
            str(raw_pdf),
            "--out",
            str(pdf_path),
            "--paper",
            paper,
            "--margin-pt",
            "{:.2f},{:.2f},{:.2f},{:.2f}".format(top, right, bottom, left),
            "--breaks",
            json.dumps(payload.get("breaks", [])),
            "--forced",
            json.dumps(payload.get("forced") or []),
        ]
        + (["--landscape"] if landscape else []),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    try:
        raw_pdf.unlink()
    except OSError:
        pass
    if cut.returncode != 0 or not pdf_path.exists():
        raise RuntimeError("分页失败：" + (cut.stderr or cut.stdout or "").strip()[-300:])
    return "webkit(macOS)"


def render_pdf(
    html_path: Path,
    pdf_path: Path,
    renderer: str,
    timeout: int,
    paper: str = "A4",
    landscape: bool = False,
    margin: str = "14mm 12mm",
) -> str:
    attempts: list[tuple[str, list[str] | None]] = []
    if renderer in ("auto", "chromium"):
        found = chromium_binary()
        if found:
            profile = tempfile.mkdtemp(prefix="sheet-chromium-")
            for headless in ("--headless=new", "--headless"):
                attempts.append(
                    (
                        found,
                        [
                            found,
                            headless,
                            "--no-sandbox",
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            "--no-first-run",
                            "--user-data-dir={}".format(profile),
                            "--print-to-pdf={}".format(pdf_path),
                            "--no-pdf-header-footer",
                            html_path.as_uri(),
                        ],
                    )
                )
    # auto 只在 macOS 上试 WebKit；显式 --renderer webkit 则照试，
    # 由 render_webkit 自己给出"这是 macOS 专有渲染器"的提示。
    if renderer in ("auto", "webkit") and (sys.platform == "darwin" or renderer == "webkit"):
        attempts.append(("webkit(macOS)", None))
    if renderer in ("auto", "soffice"):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            attempts.append(
                (
                    soffice,
                    [
                        soffice,
                        "--headless",
                        "--norestore",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(pdf_path.parent),
                        str(html_path),
                    ],
                )
            )
    errors = []
    for name, cmd in attempts:
        if pdf_path.exists():
            pdf_path.unlink()
        if cmd is None:
            try:
                return render_webkit(html_path, pdf_path, paper, landscape, margin, timeout)
            except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
                errors.append("{}: {}".format(name, exc))
                continue
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            errors.append("{}: 超时（{}s）".format(name, timeout))
            continue
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return name
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-2:]
        errors.append("{}: {}".format(name, " / ".join(tail) or "未生成 PDF"))
    if not errors:
        raise RuntimeError(
            "PDF 渲染失败：本机没有可用的渲染器"
            "（没找到 Chromium/Chrome，--renderer webkit 只在 macOS 可用，也没找到 soffice）"
        )
    raise RuntimeError("PDF 渲染失败：\n  - " + "\n  - ".join(errors))


def pdf_info(pdf_path: Path) -> dict:
    info = {}
    if shutil.which("pdfinfo"):
        proc = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True)
        for line in proc.stdout.splitlines():
            key, _, value = line.partition(":")
            info[key.strip().lower()] = value.strip()
        return info
    python = python_with_fitz()
    if python:
        proc = subprocess.run(
            [python, str(SCRIPTS_DIR / "pdfsheet.py"), "info", "--in", str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                pass
    return info


def make_preview(pdf_path: Path, prefix: Path, dpi: int, max_pages: int) -> list[Path]:
    if shutil.which("pdftoppm"):
        cmd = ["pdftoppm", "-r", str(dpi), "-png"]
        if max_pages > 0:
            cmd += ["-f", "1", "-l", str(max_pages)]
        cmd += [str(pdf_path), str(prefix)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return sorted(prefix.parent.glob(prefix.name + "-*.png"))
    python = python_with_fitz()
    if not python:
        return []
    proc = subprocess.run(
        [
            python,
            str(SCRIPTS_DIR / "pdfsheet.py"),
            "preview",
            "--in",
            str(pdf_path),
            "--prefix",
            str(prefix),
            "--dpi",
            str(dpi),
            "--max-pages",
            str(max_pages),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return []
    return [Path(item) for item in payload.get("previews", [])]


# ---------------------------------------------------------------- 输出/打印


def submit_print(pdf_path: Path, queue: str, copies: int, title: str) -> tuple[int, str]:
    lp = shutil.which("lp")
    if not lp:
        return 3, "找不到 lp 命令（CUPS 未安装）"
    cmd = [lp, "-t", title, "-n", str(copies)]
    if queue:
        cmd += ["-d", queue]
    cmd.append(str(pdf_path))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return (0 if proc.returncode == 0 else 3), (proc.stdout + proc.stderr).strip()


def cjk_warning() -> str | None:
    if not shutil.which("fc-list"):
        return None
    try:
        proc = subprocess.run(
            ["fc-list", ":lang=zh", "family"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not proc.stdout.strip():
        return "系统里没有中文字体（fc-list :lang=zh 为空），中文会渲染成方块。"
    return None


def default_name(paths: list[Path]) -> str:
    stems = [p.stem for p in paths] or ["sheet"]
    if len(stems) == 1:
        return "{}_sheet".format(stems[0])
    parents = {p.parent for p in paths}
    if len(parents) == 1:
        folder = paths[0].parent.name
        if folder:
            return "{}_sheet".format(folder)
    return "sheet_{}files".format(len(stems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sheet.py",
        description="把 Markdown / 源码 / 文本文件排成可打印、可预览的 A4 稿（HTML + PDF + PNG 预览）。",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="*", help="输入文件；用 - 表示读标准输入")
    parser.add_argument("-o", "--out", default="print-out", help="输出目录（默认 ./print-out）")
    parser.add_argument("--name", help="产物文件名前缀（默认按输入推断）")
    parser.add_argument("--title", help="文档标题")
    parser.add_argument("--paper", default="A4", choices=PAPERS)
    parser.add_argument("--landscape", action="store_true", help="横向")
    parser.add_argument("--margin", default="14mm 12mm", help="页边距（CSS 写法）")
    parser.add_argument(
        "--font",
        default=MAC_FONT if sys.platform == "darwin" else DEFAULT_FONT,
        help="正文字体栈（需含中文字体）",
    )
    parser.add_argument(
        "--cjk-font",
        default=", ".join(CJK_FAMILIES),
        help="代码区汉字用哪个楷体（按顺序取第一个装了的）；填 none 关闭",
    )
    parser.add_argument(
        "--comment-font",
        default=COMMENT_FONT,
        help="注释里西文的字体栈（含 $code_font 占位符，会被代码字体替换）",
    )
    parser.add_argument("--font-size", type=float, default=7.4, help="代码字号 pt（正文略大）")
    parser.add_argument("--no-line-numbers", action="store_true", help="代码不加行号")
    parser.add_argument(
        "--no-comment-style",
        action="store_true",
        help="注释里的西文不加细斜体（汉字楷体由 --cjk-font 决定，不受此开关影响）",
    )
    parser.add_argument(
        "--theme",
        choices=list(THEMES),
        default="light",
        help="配色：light（默认，打印省墨）/ dark / auto（屏幕跟随系统，打印仍浅色）",
    )
    parser.add_argument("--no-highlight", action="store_true", help="关闭语法高亮")
    parser.add_argument("--lang", help="强制代码区语言（默认按扩展名识别），如 go / cpp / python")
    parser.add_argument("--list-langs", action="store_true", help="列出支持高亮的语言后退出")
    parser.add_argument("--html-only", action="store_true", help="只生成 HTML，不渲染 PDF 和预览图")
    parser.add_argument("--md-renderer", choices=["auto", "md2html", "builtin"], default="auto")
    parser.add_argument(
        "--renderer", choices=["auto", "chromium", "webkit", "soffice"], default="auto"
    )
    parser.add_argument("--timeout", type=int, default=180, help="PDF 渲染超时（秒）")
    parser.add_argument("--dpi", type=int, default=100, help="预览图 DPI")
    parser.add_argument("--preview-pages", type=int, default=4, help="最多预览多少页（0=全部）")
    parser.add_argument("--no-preview", action="store_true", help="不生成 PNG 预览")
    parser.add_argument(
        "--print",
        dest="print_queue",
        nargs="?",
        const="",
        default=None,
        help="生成后送打印机；可跟队列名，缺省用系统默认打印机",
    )
    parser.add_argument("--copies", type=int, default=1, help="打印份数")
    parser.add_argument("-q", "--quiet", action="store_true")
    opts = parser.parse_args(argv)

    def say(*args):
        if not opts.quiet:
            print(*args)

    if opts.list_langs:
        print("支持高亮的语言：" + supported_langs())
        print("按扩展名自动识别；Markdown 围栏代码块按 ``` 后标注的语言高亮。")
        return 0
    if opts.lang and not normalize_lang(opts.lang):
        print(
            "错误：不认识的语言 {}（--list-langs 看支持列表）".format(opts.lang),
            file=sys.stderr,
        )
        return 2

    warning = cjk_warning()
    if warning:
        say("警告：" + warning)
    families = cjk_families(opts.cjk_font)
    if families:
        kai = cjk_font_name(families)
        if kai:
            say("代码区汉字：{}（楷体）".format(kai))
        else:
            say(
                "提示：这些楷体一个都没装（{}），代码区汉字会回落原字体。"
                " Arch 可 sudo pacman -S ttf-arphic-ukai，或把字体放进 "
                "~/.local/share/fonts 后 fc-cache -f。".format(opts.cjk_font)
            )

    if not opts.inputs:
        parser.error("至少要给一个输入文件（或用 - 读标准输入）")

    sources: list[tuple[str, Path | None, str, bool]] = []
    paths: list[Path] = []
    for item in opts.inputs:
        if item == "-":
            sources.append(("<stdin>", None, sys.stdin.read(), False))
            continue
        path = Path(item).expanduser()
        if not path.is_file():
            print("错误：找不到文件 {}".format(path), file=sys.stderr)
            return 2
        text = read_text(path)
        if text is None:
            say("跳过二进制文件：{}".format(path))
            continue
        paths.append(path)
        label = path.name if len(opts.inputs) > 1 else str(path)
        sources.append((label, path, text, path.suffix.lower() in MARKDOWN_EXT))

    if not sources:
        print("错误：没有可排版的内容", file=sys.stderr)
        return 2

    # 必须用绝对路径：render_pdf 里要把它转成 file:// URI，
    # 相对路径在 Path.as_uri() 上会直接抛 ValueError。
    out_dir = Path(opts.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    opts.name = opts.name or default_name([p.resolve() for p in paths])
    opts.title = opts.title or "{} （{} 个文件）".format(opts.name, len(sources))
    html_path = out_dir / (opts.name + ".html")
    pdf_path = out_dir / (opts.name + ".pdf")

    html_path.write_text(build_html(sources, opts), encoding="utf-8")
    say(
        "HTML: {} （主题 {}，高亮 {}）".format(
            html_path, opts.theme, "off" if opts.no_highlight else "on"
        )
    )

    if opts.html_only:
        return 0

    try:
        used = render_pdf(
            html_path,
            pdf_path,
            opts.renderer,
            opts.timeout,
            paper=opts.paper,
            landscape=opts.landscape,
            margin=opts.margin,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    info = pdf_info(pdf_path)
    pages = info.get("pages", "?")
    say("PDF : {} （{} 页，{}，渲染器 {}）".format(pdf_path, pages, info.get("page size", "?"), used))

    previews: list[Path] = []
    if not opts.no_preview:
        previews = make_preview(pdf_path, out_dir / opts.name, opts.dpi, opts.preview_pages)
        if previews:
            say("预览: {}".format(" ".join(str(p) for p in previews)))
            say("→ 打印前先看一眼预览图，确认没有截断/空白页。")

    if opts.print_queue is not None:
        code, output = submit_print(pdf_path, opts.print_queue, opts.copies, opts.title)
        say("打印: {}".format(output or "已提交"))
        if code != 0:
            print("提交打印失败：" + output, file=sys.stderr)
            return code
        say("→ 用 lpstat -o 或打印机的页数统计确认真的出纸；0 页说明驱动/PPD 不对。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
