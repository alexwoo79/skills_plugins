# 思维导图：生成、预览与导出

工具里的「思维导图」和 PPT 页面共用同一份 Markdown：**标题就是分支层级，列表就是叶子**。
导图预览（桌面端 🧠 tab）与 CLI 的 `-t xmind,opml,...` 走的是同一条 markmap 解析链路，
因此工具里看到的图就是导出的图。

## 一、Markdown → 导图的映射

| Markdown | 导图节点 |
| --- | --- |
| 第一个 `# 标题` | 根节点 |
| `## / ### / ####` | 依次往下的一级、二级、三级分支 |
| `- 列表项`（可嵌套） | 该分支下的叶子节点 |
| 纯段落、表格 | 合并成一个文本节点（表格用 ` | ` 分隔单元格） |
| `---` 分页符、`<!-- layout: xxx -->` | 忽略（对导图没有意义） |

节点文本会被规范化为纯文本：去掉 HTML 标签、`**加粗**`、`*斜体*`、`~~删除线~~`、
行内 `` `代码` ``、`[链接](url)` 只留链接文字、`![图](img)` 只留替代文字，
多行内容压成一行，超过 200 字截断成 `…`。

想调整分支粒度，就改 Markdown 的标题层级和列表嵌套；没有别的开关。

## 二、导出格式

```bash
./build/bin/markdown-ppt export -i deck.md -t xmind,opml,outline,mapsvg,markmap,offline --out ./out/
```

| 目标 | 产出文件 | 用在哪 |
| --- | --- | --- |
| `xmind` | `<标题>.xmind` | XMind 2020+ 直接打开（含结构、根主题备注） |
| `opml` | `<标题>.opml` | XMind / MindManager / iThoughts / OmniOutliner |
| `mm` | `<标题>.mm` | FreeMind / Freeplane（带分支连线配色、默认折叠层级） |
| `outline` | `<标题>.outline.md` | Markdown 大纲，可再喂回本工具当输入稿 |
| `mapsvg` | `<标题>.mindmap.svg` | 矢量图，插进 Word / 排版稿清晰不糊 |
| `markmap` | `<标题>.mindmap.html` | 交互式 HTML（markmap-autoloader，**需要联网**） |
| `offline` | `<标题>.mindmap.offline.html` | 单文件交互式 HTML（内联 d3 + markmap，**免联网**） |

导出后可以在浏览器里直接打开两个 HTML：滚轮缩放、拖拽平移、点圆点折叠；
离线版还带工具栏（展开全部 / 折叠到 N 层 / 适应窗口 / 深色 / 下载 SVG）。

## 三、参数

| 参数 | 说明 |
| --- | --- |
| `--mindmap-structure <s>` | XMind 结构：`logic.right`（默认）/ `logic.left` / `mindmap` / `tree` / `org-chart` / `timeline` / `fishbone` / `brace` |
| `--mindmap-palette <p>` | 配色预设：`business`（默认）/ `rainbow` / `mono` / `warm` / `forest` / `violet` |
| `--mindmap-colors <c>` | 自定义配色，如 `"#1D4ED8,#60A5FA"`，优先于 `--mindmap-palette` |
| `--mindmap-density <d>` | 排布密度：`compact` / `normal`（默认）/ `relaxed`（节点宽度与行距） |
| `--mindmap-levels <n>` | 只导出前 n 层（0=全部）；`outline` / `xmind` / `opml` / `mm` 都按裁剪后的树生成 |
| `--mindmap-expand <n>` | 展开层级：`-1` 全部展开（默认），`1/2/3` 只展开前 N 层；影响 SVG 与两个 HTML |

一个导图参数对所有导图目标同时生效，所以一份稿子能一次产出「交 XMind 编辑的」+
「贴进文档的 SVG」+「发给同事看的 HTML」。

## 四、桌面端「思维导图」tab

顶部工具栏（第一行）：**配色**、**密度**、**展开层级**、节点数；
第二行：**搜索**（回车定位到第一个命中并居中）、缩放/适应、**全屏**、**复制大纲**、**导出导图**。

- 配色/密度/展开层级会记在本地，下次打开沿用；配色默认「跟随主题色」，
  即按工具当前主题主色派生一组同色系深浅。
- 搜索框输入关键字即时高亮所有命中节点（黄色描边 + 底色），回车把第一个命中居中。
- 「复制大纲」把当前导图按 Markdown 列表层级复制到剪贴板，等于一次「导图 → 大纲」的逆向。
- 「导出导图」与 CLI 的导图目标一一对应：XMind / OPML / FreeMind / Markdown 大纲 /
  SVG / 离线 HTML / 交互式 HTML。

## 五、配色预设

| 名称 | 用色 |
| --- | --- |
| `business` | 深浅蓝 + 天蓝（默认，商务汇报） |
| `rainbow` | 红橙黄绿青蓝紫（多分支、强调分类） |
| `mono` | 深灰到浅灰（黑白打印友好） |
| `warm` | 琥珀 + 砖红（暖色调） |
| `forest` | 墨绿到浅绿（环保、地产、园区类主题） |
| `violet` | 紫罗兰深浅 |

色值在 `backend/mindmap/options.go`（Go 侧，用于 XMind/FreeMind/SVG/HTML）与
`frontend/src/composables/useMindmapRender.ts`（前端，用于预览与 SVG）两边保持同名同色，
改配色时两处一起改。

## 六、注意

- 导图目标只吃 Markdown 文本，**不解析 `local://` 图片**；图片引用会退化成替代文字。
- `mapsvg` 需要无头浏览器（与其它导图目标一样走 CDP）；`outline` 也走一次前端解析以保证层级一致。
- `markmap` 产出的 HTML 依赖 CDN；给内网/离线场景用 `offline`。
- 导图规模建议控制在 200 个节点以内，再大就需要先 `--mindmap-levels 3` 裁一层。
- `.xmind` 的打包刻意避开 Go 标准库的默认写法：本地文件头里带上 crc 与真实大小、
  不写 data descriptor、时间戳合法，并补上 `metadata.json`（creator + activeSheetId）与
  `Thumbnails/thumbnail.png`。只读本地文件头的解压实现（XMind 这类 Electron 客户端
  用的就是轻量解压）会把默认写法的条目当成 0 字节，打开时表现就是「无法渲染」。
  改这块时先看 `TestXMindZipIsReaderFriendly`。

## 七、尚未支持

- PNG 位图导出：无头浏览器里新导航页面拿不到合成帧，markmap 的布局回调不执行，
  截图会截到空图。需要时先用 `mapsvg` 出矢量图，再在浏览器/设计工具里导出 PNG。
- XMind 主题（theme.json）与每节点样式：当前只写结构 + 题目 + 备注，样式在 XMind 里再套。
