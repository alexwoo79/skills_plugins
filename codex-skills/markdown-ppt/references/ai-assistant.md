# 工具内置 AI 助手（提示词工作流）

工具「AI 助手」页做的三件事：填写演示信息 → 按「信息 → 模板」映射生成提示词 → 粘贴 AI 返回的 Markdown 导入解析导出。CLI 的 `markdown-ppt prompt` 复现同一份提示词（同一份 `LAYOUT_CATALOG`、同一份 `markdown-guide.md` 与 `sample_all_layouts.md`，输出已实测逐字节一致）。

## 何时用它

- 只有素材、没写成 Markdown：先生成提示词，再让 AI（或本 Agent）按提示词产出 Markdown，最后 `export`。
- 想与工具内 AI 助手得到完全一致的提示词（便于人工核对、复用历史提示词）。
- 已经有整理好的大纲 Markdown：跳过提示词，直接写稿 + `export`。

## 输入字段

| 字段 | CLI | 说明 |
| --- | --- | --- |
| `title` | `--title` | 演示主题，同时作为提示词与默认文件名 |
| `subtitle` / `org` / `location` / `date` | `--subtitle` / `--org` / `--location` / `--date` | 封面副标题与元信息 |
| `targetPages` | `--pages` | 目标**总**页数，含封面、目录、结束页 |
| `layouts` | `--layouts` | 允许使用的内容布局 id，逗号分隔；缺省用工具默认全集 |
| `outline` | `--outline` | 内容大纲，多行用换行分隔；`@文件路径` 可读取文件 |
| `extra` | `--extra` | 额外要求（口径、数据来源、禁忌等） |
| `language` | `--lang` | `zh`（默认）或 `en` |
| `includeSample` | `--no-sample` | 是否在提示词中附带完整 `sample_all_layouts.md` 样例，默认附带 |

也可以用 `--spec spec.json` 一次性给出（等价于工具内表单），CLI 参数优先级高于 spec：

```json
{
  "title": "2026 年度城市更新战略汇报",
  "subtitle": "战略与投资部",
  "org": "XX 控股集团",
  "location": "上海",
  "date": "2026-09-20",
  "targetPages": 12,
  "layouts": ["datacards", "timeline", "3flow", "compare", "table", "text"],
  "outline": "政策背景\n市场格局\n产业链结构\n资金渠道\n实施路径",
  "extra": "数据要具体，无来源时标注「约」",
  "language": "zh",
  "includeSample": true
}
```

## 信息 → 模板映射（选布局时的判断依据）

| 布局 id | 适合的信息 | 自动识别 | 建议显式声明 |
| --- | --- | --- | --- |
| `datacards` | 数值指标、关键数据并列 | `标签：数值` ≥3 行 | 是 |
| `3flow` | 三步流程 / 三段式环节 | 恰好 3 个编号项 | 否 |
| `5col` | 5~8 个并列要点、渠道、举措 | 5~8 个列表项 | 否 |
| `compare` | 两方方案、前后、优劣势对照 | 无 | 是 |
| `2info` | 两个分组的要点面板 | `###` 分组且列表项 ≥4 | 是 |
| `radial` | 中心主题 + 外围要点 | 无 | 是 |
| `timeline` | 时间顺序事件、里程碑 | 含日期关键词的行 ≥3 | 否 |
| `mixed` | 图文交替（Z 字形） | 图片行 + 文本行并存 | 否 |
| `table` | 结构化多列表格 | 出现表格分隔行 | 否 |
| `text` | 连续自然段、结论、说明 | 只有连续文本 | 是 |

固定页（`cover` / `contents` / `transition` / `end`）不参与勾选，由文档结构自动生成。字段级语法见 [markdown-format.md](markdown-format.md)。

## 提示词强制 AI 遵守的输出规范

1. 只输出 Markdown 正文，不要解释、前言，也不要给整篇套代码围栏。
2. 每一页用独立一行 `---` 分隔，每页都有 `## 标题`。
3. 按信息类型选模板，标注「建议显式」的布局必须写 `<!-- layout: ... -->`。
4. 数据具体可信；无依据时给合理估算并标注「约/估算」。
5. 无真实图片时用 `![配图说明](https://placehold.co/600x400?text=描述 =600x400)` 占位。
6. 篇幅控制在目标总页数附近（含封面、目录、结束页）。

## 拿到 Markdown 之后

```bash
markdown-ppt parse -i deck.md --pretty | head -40          # 结构/页数自检
markdown-ppt export -i deck.md -t pptx,html --out ./out/   # 导出
```

需要把已有 Markdown 的封面信息回填成提示词输入时，可用工具同一实现 `extractCoverInfo`（`frontend/src/composables/usePromptBuilder.ts`）读取标题、副标题、汇报单位、地点、日期。
