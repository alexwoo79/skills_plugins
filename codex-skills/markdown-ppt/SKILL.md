---
name: markdown-ppt
description: 用 Markdown PPT 工具的无头 CLI 把 Markdown 转成 PPTX/SVG/HTML/Reveal.js/Word 演示文稿，按该工具的排版语法撰写或改造 Markdown，并复用工具内置 AI 助手生成提示词。涉及 Markdown 汇报稿转 PPT、批量文档转换、企业级 PPTX 生成、演示文稿排版规范时使用；纯 Word/Excel/PDF 文档处理不要使用。
metadata:
  short-description: Markdown → PPTX/HTML 演示文稿转换
---

# Markdown PPT 转换

把 Markdown 变成可交付的演示文稿。入口是工具自带的无头 CLI（Go 单二进制，与桌面端共用同一份渲染代码）：

- 解析：`backend/parser`（Go）
- 渲染：二进制内嵌的前端产物 + 无头 Chromium（CDP），与桌面端「导出 PPTX / 导出 HTML」同源
- 提示词：与工具「AI 助手」页输出完全一致（纯 Go 实现，毫秒级）

不要用 python-pptx / pandoc 之类的替代方案重写排版；版式与主题只有这条链路才还原得出来。

## 命令入口

| 入口 | 来源 |
| --- | --- |
| `<skill>/bin/markdown-ppt` | 独立安装包（`-cli` / `-cli-cross`）安装后 |
| `build/bin/markdown-ppt` | 仓库内 `make cli`（交叉编译用 `make cli-cross`） |

```bash
./build/bin/markdown-ppt <命令> [选项]
```

首次使用先跑 `doctor` 自检（浏览器、内嵌前端、图片目录）。三个子命令：

| 命令 | 作用 | 需要浏览器 |
| --- | --- | --- |
| `parse` | Markdown → 页面 JSON（诊断页数与结构） | 否 |
| `prompt` | 生成工具 AI 助手的提示词 | 否 |
| `export` | 导出 pptx / svg / html / reveal / docx，以及思维导图 xmind / opml / mm / outline / mapsvg / markmap / offline | 是 |

## 主流程

### 常见任务的命令速查

| 需求 | 命令 |
| --- | --- |
| 汇报用 PPT（默认模板） | `export -i deck.md -t pptx --out ./out/` |
| 汇报用 PPT（中铁建模板） | `export -i deck.md -t pptx --preset crcc --out ./out/` |
| 中文书稿风 HTML（暖纸 + 宋体） | `export -i doc.md -t html --preset book --out ./out/` |
| 一份稿子出三件套：PPT + HTML + 导图 HTML | `export -i deck.md -t deck --preset book --out ./out/` |
| 导图交 XMind 编辑 | `export -i deck.md -t xmind --out ./out/` |
| 导图三件套：XMind + 大纲 + 离线 HTML | `export -i deck.md -t xmind,outline,offline --mindmap-palette rainbow --out ./out/` |
| 全套格式（13 种，含 XMind/OPML/FreeMind/大纲/SVG/Reveal/Docx） | `export -i deck.md -t all --out ./out/` |
| 先看结构与页数 | `parse -i deck.md --pretty \| head -20` |
| 从素材生成 AI 提示词 | `prompt --title "主题" --pages 12 -o prompt.md` |

### A. 已有 Markdown → 直接导出

```bash
./build/bin/markdown-ppt export -i deck.md -t pptx,html --theme default --out ./out/

# 思维导图：XMind / OPML / FreeMind / Markdown 大纲 / SVG / 交互式 HTML / 离线 HTML
./build/bin/markdown-ppt export -i deck.md -t xmind,opml,mm,outline,mapsvg,markmap,offline --out ./out/
```

### B. 只有素材 → 生成提示词 → 产出 Markdown → 导出

工具 AI 助手的三步工作流，在 CLI 里同样成立：

```bash
# 1) 生成与工具 AI 助手一致的提示词
./build/bin/markdown-ppt prompt --title "2026 战略汇报" \
  --pages 12 --layouts datacards,timeline,3flow,table,compare \
  --outline "政策背景
市场格局
实施路径" -o prompt.md

# 2) 由 AI（或本 Agent）按提示词产出 deck.md

# 3) 先校验结构，再导出
./build/bin/markdown-ppt parse -i deck.md --pretty | head -20
./build/bin/markdown-ppt export -i deck.md -t pptx --out ./out/
```

自己撰写 Markdown 时按下面「排版要点」写；需要全部布局语法与示例时读 [references/markdown-format.md](references/markdown-format.md)。`prompt` 的输入字段、布局选择规则与输出规范见 [references/ai-assistant.md](references/ai-assistant.md)；思维导图（层级映射、七种导出格式、配色与参数）见 [references/mindmap.md](references/mindmap.md)。

## 排版要点（决定导出效果的部分）

- 分页只有一种：独立一行的 `---`。`##` / `###` 是页内标题，不分页。
- 文档开头第一个 `# 标题` 自动成为封面，其下可写 `汇报单位：` / `地点：` / `日期：`（或 `org:` / `location:` / `date:`）。
- 系统自动补两页：封面之后自动生成目录页，缺少致谢时自动补结束页。**最终页数 = 封面 + 目录 + 内容页 + 结束页**，写 `--pages N` 时按此估算。
- 只有 `## 标题` 没有正文的页 = 过渡页；标题为「谢谢 / 感谢 / Thank You / Q&A」等 = 结束页。
- 版式可自动识别，但 datacards / compare / 2info / radial / text **建议显式写** `<!-- layout: xxx -->`，避免歧义。
- 自动识别优先级：图片+文本 → mixed；Markdown 表格 → table；「标签：数值」≥3 行 → datacards；日期行 ≥3 → timeline；`###` 分组且列表项 ≥4 → 2info；恰好 3 个编号项 → 3flow；5~8 个列表项 → 5col；纯段落 → text。

## export 选项

| 选项 | 说明 |
| --- | --- |
| `-i, --input` | 输入 Markdown（`-` 表示读 stdin） |
| `-o, --out` | 输出目录（按文档标题命名）或文件前缀；缺省写入当前目录 |
| `-t, --to` | `pptx,svg,html,reveal,docx` 或思维导图 `xmind,opml,mm,outline,mapsvg,markmap,offline`，逗号分隔，默认 `pptx`；成套简写 `deck`（pptx+html+markmap）、`all`（全部格式） |
| `--preset` | 样式预设：`book` 书稿（paper + 宋体栈）、`academic` 学术（professional + 论文样式 + 宋体栈）、`crcc` 企业模板（crcc 主题 + paper + 宋体栈）。单独指定的参数优先于预设 |
| `--theme` | `default` / `professional` / `simple` / `creative` / `crcc`（crcc 为 10×5.625in，其余 13.333×7.5in） |
| `--title` | 输出文件名与文档标题，默认取封面标题 |
| `--images-dir` | `local://` 图片目录，默认 `~/.config/markdown-ppt/images`（目录内的 `registry.json` 会被自动读取） |
| `--image-map` | `local://` 短引用 → 文件名 的映射 JSON，如 `{"img-1785912671326-0":"cover.png"}`（优先级最高） |
| `--reveal-theme` / `--reveal-transition` | Reveal.js 主题（black/white/beige/night…）与切换动画 |
| `--html-style` | `-t html` 的文档样式，同工具预设：`github`（默认）、`paper` 暖纸书卷、`academic` 学术论文、`byword` 写作稿纸、`magazine`、`ocean`、`terminal`、`swiss`、`foghorn`、`fireball`、`solarized-light/dark`、`midnight` |
| `--font-family` / `--font-size` | HTML 正文字体（`serif` / `sans` / `mono` / `songti` 宋体 / `heiti` / `kaiti`）与字号 |
| `--font-stack` | 自定义 CSS 字体栈，覆盖预设字体。中文书稿推荐 `"Songti SC","Noto Serif CJK SC","宋体",SimSun,Georgia,serif` |
| `--wallpaper` / `--html-theme` / `--no-toc` | 背景（`paper` / `grid` / `notebook` / `dark`）、明暗主题、关闭目录 |

`local://` 是桌面端的本地图片短引用。图片实体在 `~/.config/markdown-ppt/images/`，映射表由桌面端写入同目录的 `registry.json`，CLI 自动读取；也可用 `--images-dir` 按文件名匹配或 `--image-map` 显式指定。仍解析不到的引用会替换为透明像素并告警，不会中断导出。

`docx` 目标输出的是 Word 兼容 HTML（与工具内置 Docx 导出一致，Word 可直接打开）；需要标准 OOXML 文档时另行处理。

## 思维导图导出

导图由 Markdown 的**标题与列表层级**生成（与桌面端「🧠 思维导图」tab 同一份 markmap 解析结果），纯段落与表格会成为单个文本节点。七种目标可以一起写：

```bash
./build/bin/markdown-ppt export -i deck.md -t xmind,opml,mm,outline,mapsvg,offline --out ./out/
```

| 目标 | 产出 | 可导入/打开 |
| --- | --- | --- |
| `xmind` | `<标题>.xmind`（XMind 2020+ 的 content.json + manifest + metadata 压缩包） | XMind 2020 及以上；结构可用 `--mindmap-structure` 指定 |
| `opml` | `<标题>.opml`（OPML 2.0 大纲，带创建时间） | XMind、MindManager、iThoughts、OmniOutliner |
| `mm` | `<标题>.mm`（FreeMind/Freeplane XML，带分支连线配色与默认折叠） | FreeMind、Freeplane |
| `outline` | `<标题>.outline.md`（Markdown 大纲） | 再当作本工具的输入稿，或贴进文档 |
| `mapsvg` | `<标题>.mindmap.svg`（矢量图，贴合内容尺寸） | Word / 排版稿 / 设计工具，缩放不糊 |
| `markmap` | `<标题>.mindmap.html`（markmap 官方 autoloader 单页，CDN 加载） | 浏览器直接打开，可缩放/按层折叠 |
| `offline` | `<标题>.mindmap.offline.html`（内联 d3 + markmap 的单文件） | 浏览器直接打开，免联网；带展开/折叠/深色/下载 SVG 工具栏 |

导图专用参数（一次给全，对所有导图目标生效）：

| 参数 | 说明 |
| --- | --- |
| `--mindmap-structure <s>` | XMind 结构：`logic.right`（默认）/ `logic.left` / `mindmap` / `tree` / `org-chart` / `timeline` / `fishbone` / `brace` |
| `--mindmap-palette <p>` | 配色：`business`（默认）/ `rainbow` / `mono` / `warm` / `forest` / `violet` |
| `--mindmap-colors <c>` | 自定义配色（`"#1D4ED8,#60A5FA"`），优先于 palette |
| `--mindmap-density <d>` | 排布密度：`compact` / `normal`（默认）/ `relaxed` |
| `--mindmap-levels <n>` | 只导出前 n 层（0=全部） |
| `--mindmap-expand <n>` | 展开层级：`-1` 全部展开（默认），`1/2/3` 只展开前 N 层 |

要点：

- 导图目标只使用 Markdown 文本，不解析 `local://` 图片。
- 节点内容会被规范化为纯文本（去 HTML 标签与实体、去 `**加粗**`/`[链接](url)` 等 Markdown 标记、合并空白、超长截断到 200 字），因此表格、加粗等富文本在导图里是纯文字。
- 导出导图同样需要一个 Chromium 内核浏览器：树结构由前端 markmap-lib 解析，保证与预览一致。
- 想改导图的分支粒度，调 Markdown 的标题/列表嵌套即可；`-t xmind` 这类目标与 pptx 目标可以放在同一条命令里一起产出。
- 完整说明（映射规则、配色表、桌面端 tab 功能、限制）见 [references/mindmap.md](references/mindmap.md)。

## prompt 选项

| 选项 | 说明 |
| --- | --- |
| `--title` / `--subtitle` / `--org` / `--location` / `--date` | 演示信息 |
| `--pages` | 目标总页数（含封面、目录、结束页） |
| `--layouts` | 允许使用的布局 id，逗号分隔；缺省用工具默认全集 |
| `--outline` | 内容大纲，多行用换行分隔；`@文件路径` 读取文件 |
| `--extra` / `--lang` | 额外要求 / 输出语言（`zh` 默认、`en`） |
| `--spec` | 一次性给出输入的 JSON（等价于工具内表单） |
| `-o, --out` | 输出文件；缺省打印到 stdout |

## 运行注意

- `export` 需要一个 **Chromium 内核浏览器**（Chrome / Chromium / Edge / Brave / Vivaldi / Opera 任一），程序自动探测系统已装的那个，不随包分发浏览器。Firefox / Safari 不支持——这条链路依赖 CDP。
  - 指定/覆盖：`MDPPT_CHROMIUM=/path/to/chrome`（路径不存在会直接报错，不再静默回退）
  - `doctor` 会列出探测到的全部浏览器，首选排在最前
- `export` 必须启动浏览器：沙箱内会被内核拦截（SIGTRAP）或禁止监听本地端口（EPERM），请以沙箱外权限运行，或直接说一句「用沙箱外权限跑 markdown-ppt export」。
- 思维导图目标（`xmind` / `opml` / `mm` / `outline` / `mapsvg` / `markmap` / `offline`）同样需要浏览器（树由前端 markmap-lib 解析）；只想要纯文本格式时仍要保留浏览器依赖。
- 渲染用的是二进制内嵌的前端产物：改完前端需要重新 `make cli`（或重跑打包脚本），否则二进制里还是旧版渲染代码。
- 导出前想知道页数与结构，先用 `parse` 检查，比反复导出快得多。

## 打包分发（独立安装包）

```bash
python3 skills/markdown-ppt/package/build_package.py            # 当前平台：-cli 包（约 10 MB）
python3 skills/markdown-ppt/package/build_package.py --cross    # 三平台：-cli-cross 包（约 31 MB）
```

产物在 `package/dist/`，包内 `install.sh` 会把 skill 复制到 `~/.codex/skills/markdown-ppt`（含 `bin/markdown-ppt`）并运行 `doctor`：

```bash
./install.sh                          # 装到 ~/.codex/skills/markdown-ppt
CODEX_HOME=/path/.codex ./install.sh  # 自定义 Codex 目录
./install.sh --link /path/to/wails    # 本机开发：软链到仓库里的 skill
```

打包脚本会先执行前端构建（`npm run build`，产出含 `headless.html` 的 dist），再 `go build` 把产物嵌进二进制。跳过前端构建用 `--skip-frontend-build`（需已有 dist）。
