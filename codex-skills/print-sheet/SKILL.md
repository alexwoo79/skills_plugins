---
name: print-sheet
description: 把 Markdown、源码或文本文件排成"可预览再打印"的成品：A4 排版 HTML、PDF 和逐页 PNG 预览，并可按需送 CUPS 打印机。代码注释里的汉字排成楷体、西文排成斜体。当用户要把代码/笔记/文档打印成纸质稿、要一份排版整齐的代码审阅稿、或需要先预览再出纸的 PDF 时使用。
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
| `--no-comment-style` | 关掉注释的楷体/斜体（默认开） |
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
- **代码注释的字体。** 注释里的汉字用楷体（macOS: `Kaiti SC`/STKaiti；Linux: `KaiTi`→serif 兜底），西文（含 `//`、`#` 这些注释标记）用**细体斜体**：macOS 上优先 `Helvetica Neue` Light Italic（`font-weight: 300`，想更瘦可改 200/100），取不到就退到 `Avenir Next`，再退到代码等宽斜体——所以 Linux 上观感仍是等宽斜体。注释按扩展名/文件名识别：C 家族 `// /* */`、脚本 `#`、SQL/Haskell `--`、HTML `<!-- -->`、汇编 `;`、TeX `%`、vim `"`；字符串里的 `#`、`//` 不会被误判。`--no-comment-style` 可关掉。Markdown 里的围栏代码块不做这个处理。
- **长行必须折行，不能裁掉。** 脚本用 `white-space: pre-wrap` + 悬挂缩进；自己改模板时别换成 `pre`，否则超宽行在纸上会整段消失。
- **渲染器：** Linux 用 headless Chromium（沙箱内需要 `--no-sandbox` 和可写的 `--user-data-dir`，脚本已带）。macOS 上没装 Chromium 也能用：`scripts/wk2pdf.js` 走系统自带的 WebKit 渲染出整页 PDF，再由 `scripts/pdfsheet.py`（PyMuPDF）按纸型切页并出预览图——所以 macOS 上需要 `python3 -c "import fitz"` 可用（`pip install pymupdf`）。两条路都要在沙箱外跑，脚本会提示。`--renderer soffice` 只是最后的兜底，CSS 支持差。
- **macOS 默认字体**是 `"SF Mono", Menlo, "PingFang SC", "Songti SC", monospace`（Linux 上是 Noto Sans Mono CJK + JetBrainsMono Nerd Font）。
- **Markdown 渲染依赖 `md2html`（md4c）**，缺了会自动退回内置的最小渲染器（`--md-renderer builtin` 可强制）。
- **打印是外部副作用**，只在用户明确要求时执行；打印前务必先看预览，避免整叠废纸。
- 大文件（几百行以上）多次分页属正常；`--font-size` 调小可以压页数。
