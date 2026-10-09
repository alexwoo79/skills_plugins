---
name: print-sheet
description: 把 Markdown、源码或文本文件排成"可预览再打印"的成品：A4 排版 HTML、PDF 和逐页 PNG 预览，并可按需送 CUPS 打印机。代码文件与 Markdown 围栏代码块里的汉字（含注释）排成楷体、西文仍是等宽，注释里的西文再用细斜体。当用户要把代码/笔记/文档打印成纸质稿、要一份排版整齐的代码审阅稿、或需要先预览再出纸的 PDF 时使用。
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
python3 scripts/sheet.py src/*.py --print            # 生成后送系统默认打印机
```

| 参数 | 说明 |
| --- | --- |
| `-o DIR` | 输出目录（默认 `./print-out`） |
| `--title` / `--name` | 文档标题 / 产物文件名前缀（默认按输入目录推断） |
| `--font-size` | 代码字号 pt（默认 7.4），Markdown 正文自动大 2pt |
| `--no-line-numbers` | 去掉行号（适合散文、说明文档） |
| `--no-comment-style` | 注释里的西文不加细斜体（默认加；汉字楷体不受影响） |
| `--paper` `--landscape` `--margin` | 纸张、横排、页边距 |
| `--preview-pages N` / `--no-preview` | 预览页数（默认 4）/ 不生成预览 |
| `--print [QUEUE]` / `--copies N` | 送打印机；队列名可省略（用默认打印机） |
| `--renderer` | `auto`（默认，依次试 Chromium → macOS WebKit → soffice）/ `chromium` / `webkit` / `soffice` |

## 工作流

1. 确定输入文件；用户列了哪些就排哪些，不要顺手加别的文件。
2. 跑脚本生成 HTML / PDF / PNG。
3. **先看预览图**（`view_image <name>-1.png`）：检查长行是否折断或被裁、中文有没有变方块、有没有空白页、表格/代码块有没有溢出到纸外。
4. 只有用户要打印时才加 `--print`，并按 [references/printing.md](references/printing.md) 验证真的出纸。
5. 交付时给出 HTML / PDF / 预览图的绝对路径，说明对应关系（哪个文件在第几页）。

## 必须注意的点

- **字体要带中文字形。** 默认字体栈是本机的 `Noto Sans Mono CJK SC` + `JetBrainsMono Nerd Font`；`fc-list :lang=zh` 为空时脚本会警告，此时中文会印成方块。散文类文档可以用 `--font 'Noto Serif CJK SC, serif'`。
- **代码里的汉字走楷体。** 代码区（源码、配置、文本文件，以及 Markdown 的围栏代码块和行内代码）用一条 `unicode-range` 只覆盖 CJK 码位的 `@font-face`，把汉字交给楷体，**西文、数字、符号仍按原来的等宽字体走**，所以代码列宽和字距不变（汉字在楷体里同样是全角 1em）。注释和字符串里的汉字一视同仁，不止注释。
- **楷体只认系统自带的，不绑定第三方字体。** `@font-face` 的 `src` 依次找 `Kaiti SC`/`Kaiti TC`/`STKaiti`（macOS）、`KaiTi`（Windows）、`AR PL UKai`、`TW-Kai`（Linux 发行版）——**默认不含霞鹜文楷等外部下载字体**。一台机器这些名字一个都取不到时，`@font-face` 整体失效，中文回落原字体（不会变方块），也就是「代码汉字楷体」这个效果自动消失；想强制用某个字体（例如霞鹜文楷），把它的族名加进 `src` 最前面即可。
- **想装楷体再验证：** 用系统包管理器装最省事（Debian/Ubuntu 是 `fonts-arphic-ukai`，Arch 官方仓库没有，需走 AUR）；不想动 root 就把 ttf/otf 放进 `~/.local/share/fonts/<名字>/` 再跑 `fc-cache -f`，然后 `fc-list :lang=zh | grep -i kai` 确认。注意 `Noto Sans Kaithi` 不是楷体，别被名字骗了。**Markdown 的正文不受影响**，仍是 `--font` 指定的字体（行内代码除外）。
- **注释里的西文是细斜体。** `//`、`#` 这些标记和西文注释用 `Helvetica Neue` Light Italic（`font-weight: 300`，想更瘦可改 200/100），取不到就退到 `Avenir Next`，再退到代码等宽斜体——所以 Linux 上观感是等宽斜体。注释按扩展名/文件名识别：C 家族 `// /* */`、脚本 `#`、SQL/Haskell `--`、HTML `<!-- -->`、汇编 `;`、TeX `%`、vim `"`；字符串里的 `#`、`//` 不会被误判。**Markdown 的围栏代码块按语言标记（`cpp`、`python` 等）套同一套规则**，认不出的语言、以及没写语言的围栏按 generic 处理（行首 `#` 与 `//`）。`--no-comment-style` 只关掉这层斜体，围栏代码块里的汉字楷体不受影响。
- **长行必须折行，不能裁掉。** 脚本用 `white-space: pre-wrap` + 悬挂缩进；自己改模板时别换成 `pre`，否则超宽行在纸上会整段消失。
- **渲染器：** Linux 用 headless Chromium（沙箱内需要 `--no-sandbox` 和可写的 `--user-data-dir`，脚本已带）。macOS 上没装 Chromium 也能用：`scripts/wk2pdf.js` 走系统自带的 WebKit 渲染出整页 PDF，再由 `scripts/pdfsheet.py`（PyMuPDF）按纸型切页并出预览图——所以 macOS 上需要 `python3 -c "import fitz"` 可用（`pip install pymupdf`）。两条路都要在沙箱外跑，脚本会提示。`--renderer soffice` 只是最后的兜底，CSS 支持差。
- **macOS 默认字体**是 `"SF Mono", Menlo, "PingFang SC", "Songti SC", monospace`（Linux 上是 Noto Sans Mono CJK + JetBrainsMono Nerd Font）。
- **Markdown 渲染依赖 `md2html`（md4c）**，缺了会自动退回内置的最小渲染器（`--md-renderer builtin` 可强制）。
- **打印是外部副作用**，只在用户明确要求时执行；打印前务必先看预览，避免整叠废纸。
- 大文件（几百行以上）多次分页属正常；`--font-size` 调小可以压页数。
