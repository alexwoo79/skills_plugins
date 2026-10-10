---
name: print-sheet
description: 把 Markdown、源码或文本文件排成"可预览再打印"的成品：A4 排版 HTML、PDF 和逐页 PNG 预览，并可按需送 CUPS 打印机。HTML 可切换明暗主题、内置语法高亮；代码区（含注释）的汉字排成楷体、西文仍是等宽，注释里的西文再用细斜体。当用户要把代码/笔记/文档打印成纸质稿、要一份排版整齐的代码审阅稿、或需要先预览再出纸的 PDF 时使用。
---

# Print Sheet

把若干 `.md` / 源码 / 文本文件排成三件产物：

| 产物 | 用途 |
| --- | --- |
| `<name>.html` | A4 打印样式；屏幕上显示为带页边距的白纸（和 PDF 一致），打印时边距走 `@page` |
| `<name>.pdf` | 送打印机的成品 |
| `<name>-N.png` | 逐页预览图，出纸前先看一眼 |

Markdown 走排版渲染（标题、列表、表格、GFM 任务列表、代码块），代码和文本逐行排版并带行号；每个输入文件一节、各自另起一页。

## 用法

```bash
python3 scripts/sheet.py [选项] 文件...
python3 scripts/sheet.py counter.h counter.cpp main.cpp Main.qml -o print-out --title "day4 源码"
python3 scripts/sheet.py notes.md --font 'Noto Serif CJK SC, serif' --font-size 9
python3 scripts/sheet.py src/main.go --theme dark --html-only   # 深色、只出 HTML
python3 scripts/sheet.py doc.md --theme auto                    # 屏幕跟系统深浅色，打印仍浅色
python3 scripts/sheet.py src/*.py --print            # 生成后送系统默认打印机
```

| 参数 | 说明 |
| --- | --- |
| `-o DIR` | 输出目录（默认 `./print-out`） |
| `--title` / `--name` | 文档标题 / 产物文件名前缀（默认按输入目录推断） |
| `--font-size` | 代码字号 pt（默认 7.4），Markdown 正文自动大 2pt |
| `--no-line-numbers` | 去掉行号（适合散文、说明文档） |
| `--cjk-font` | 代码区汉字用哪个楷体（按顺序取第一个装了的；默认 macOS `Kaiti SC`/`STKaiti`、Windows `KaiTi`、Linux `AR PL UKai`/`TW-Kai`）；填 `none` 关闭 |
| `--comment-font` / `--no-comment-style` | 注释里西文的字体栈（默认细字重斜体，`$code_font` 会被替换成代码字体）/ 只关掉这层斜体 |
| `--theme light\|dark\|auto` | 配色（默认 `light`）：`dark` 适合屏幕阅读，`auto` 屏幕跟随系统、打印仍浅色 |
| `--lang NAME` / `--no-highlight` | 强制代码区语言（默认按扩展名识别）/ 关掉语法高亮；`--list-langs` 列出支持的语言 |
| `--html-only` | 只生成 HTML，不渲染 PDF 和预览图 |
| `--paper` `--landscape` `--margin` | 纸张、横排、页边距（页边距同时决定 HTML 在浏览器里的留白） |
| `--preview-pages N` / `--no-preview` | 预览页数（默认 4）/ 不生成预览 |
| `--print [QUEUE]` / `--copies N` | 送打印机；队列名可省略（用默认打印机） |
| `--renderer` | `auto`（默认，依次试 Chromium → macOS WebKit → soffice）/ `chromium` / `webkit` / `soffice` |

## HTML 主题与语法高亮

高亮内置于脚本（零依赖，不需要 Pygments）：按扩展名识别 go / c / cpp / python / js / ts / qml / rust / java / kotlin / cs / swift / shell / lua / sql / json / yaml / toml / ini / make / docker / css / html 等，Markdown 里带语言标注的围栏代码块同样高亮；认不出的语言退回纯文本，`--no-highlight` 可以整体关掉。

颜色走 CSS 变量 + `.hl` token 类（`k` 关键字、`kt` 类型、`nf` 函数、`s` 字符串、`c` 注释……），改配色就改脚本里的 `THEME_VARS` / `TOKEN_COLORS`。深色主题会把整页铺成深色，出纸请用 `light` 或 `auto`（`auto` 的深色只挂在 `@media screen` 下，PDF 仍是浅色）。

## 中文字体与注释

- **代码里的汉字走楷体。** 代码区（源码、配置、文本文件，以及 Markdown 的围栏代码块和行内代码）用一条 `unicode-range` 只覆盖 CJK 码位的 `@font-face`，把汉字交给楷体，**西文、数字、符号仍按原来的等宽字体走**，所以代码列宽和字距不变（汉字在楷体里同样是全角 1em）。注释和字符串里的汉字一视同仁，不止注释。
- **楷体只认系统自带的，不绑定第三方字体。** 机器上这些族名一个都取不到时，`@font-face` 整体失效，中文回落原字体（不会变方块），也就是"代码汉字楷体"这个效果自动消失；想强制用某个字体（例如霞鹜文楷），用 `--cjk-font 'LXGW WenKai, AR PL UKai CN'` 把它加在最前面。**Markdown 的正文不受影响**，仍是 `--font` 指定的字体（行内代码除外）。
- **注释里的西文是细斜体。** `//`、`#` 这些标记和西文注释用细字重斜体（`font-weight: 300`，想更瘦可改 200/100）：默认先找 `Noto Sans CJK SC Light`（Linux 上有 Light 字重），再退到 `Helvetica Neue` / `Avenir Next`（macOS 的 Light Italic），最后退到代码等宽斜体。注释按语言识别：C 家族 `// /* */`、脚本 `#`、SQL/Haskell `--`、Lua `--[[ ]]`、HTML `<!-- -->`、汇编 `;`、TeX `%`、Lisp `;`、vim `"`；字符串里的 `#`、`//` 不会被误判。**Markdown 的围栏代码块套同一套规则**，认不出的语言和没写语言的围栏按 generic（行首 `#` 与 `//`）处理。`--no-comment-style` 只关掉这层斜体，汉字楷体不受影响。

## 工作流

1. 确定输入文件；用户列了哪些就排哪些，不要顺手加别的文件。
2. 跑脚本生成 HTML / PDF / PNG（只要 HTML 就加 `--html-only`）。
3. **先看预览图**（`view_image <name>-1.png`）：检查长行是否折断或被裁、中文有没有变方块、有没有空白页、表格/代码块有没有溢出到纸外。
4. 只有用户要打印时才加 `--print`，并按 [references/printing.md](references/printing.md) 验证真的出纸。
5. 交付时给出 HTML / PDF / 预览图的绝对路径，说明对应关系（哪个文件在第几页）。

## 必须注意的点

- **字体要带中文字形。** 默认字体栈是本机的 `Noto Sans Mono CJK SC` + `JetBrainsMono Nerd Font`（macOS 是 `SF Mono` + `PingFang SC`/`Songti SC`）；`fc-list :lang=zh` 为空时脚本会警告，此时中文会印成方块。散文类文档可以用 `--font 'Noto Serif CJK SC, serif'`。
- **想装楷体再验证：** 系统包管理器最省事（Debian/Ubuntu 是 `fonts-arphic-ukai`，Arch 是 `ttf-arphic-ukai`）；不想动 root 就把 ttf/otf 放进 `~/.local/share/fonts/<名字>/` 再跑 `fc-cache -f`，然后 `fc-list :lang=zh | grep -i kai` 确认。注意 `Noto Sans Kaithi` 不是楷体。
- **长行必须折行，不能裁掉。** 脚本用 `white-space: pre-wrap` + 悬挂缩进；自己改模板时别换成 `pre`，否则超宽行在纸上会整段消失。
- **渲染器：** Linux 用 headless Chromium（沙箱内需要 `--no-sandbox` 和可写的 `--user-data-dir`，脚本已带）。macOS 上没装 Chromium 也能用：`scripts/wk2pdf.js` 走系统自带的 WebKit 渲染出整页 PDF，再由 `scripts/pdfsheet.py`（PyMuPDF）按纸型切页并出预览图——所以 macOS 上需要 `python3 -c "import fitz"` 可用（`pip install pymupdf`）。`--renderer soffice` 只是最后的兜底，CSS 支持差。
- **Markdown 渲染依赖 `md2html`（md4c）**，缺了会自动退回内置的最小渲染器（`--md-renderer builtin` 可强制）。
- **打印是外部副作用**，只在用户明确要求时执行；打印前务必先看预览，避免整叠废纸。
- 大文件（几百行以上）多次分页属正常；`--font-size` 调小可以压页数。
