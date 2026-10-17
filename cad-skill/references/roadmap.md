# 优化路线与状态

这份清单对应 2026-10 的一轮优化：参考"图像→DXF"调研里"几何层 / 语义层 / 构件与标注层"
的拆分，但只采纳适合本 skill 的部分。每条都配了离线回归测试。

## 已完成

| 优先级 | 项目 | 落点 | 验收 |
| --- | --- | --- | --- |
| P0 | 描图门的合页侧/开向 | `image_to_calls.infer_door_swings` | 四向门 4/4；见 `test_door_swing_inferred_from_arc` |
| P0 | 移除服务端通道与相关脚本/说明 | `SKILL.md`、`references/` | 全仓无服务端引用 |
| P0 | 清理 `.DS_Store` / 生成物 | `.gitignore`、`cad_output/.gitkeep` | 仓库干净 |
| P1 | 一份 plan.json 四个出口 | `scripts/plan_render.py` | calls/scr/DXF 图元数一致；SVG 合法 |
| P1 | 离线 SVG 预览 | `plan_render.write_svg` | XML/图元/尺寸文字/待复核圈 |
| P1 | 轴网 `axis` | `wall_plan.grid_calls` | 自动/显式轴位与轴号 |
| P1 | 自动尺寸 `dims` | `wall_plan.dimension_calls` | 分段 + 总尺寸，DXF 为真 DIMENSION |
| P1 | 门窗图块（DXF） | `plan_render.write_dxf --blocks` | 25 个图元 → 10 个 INSERT |
| P1 | 待复核清单 | `needs_review` → `review` | 汇总到 JSON 与 SVG 红圈 |
| P1+ | 填充墙洞口（墙带占空比） | `image_to_calls.detect_band_openings` | 合成灰墙 + 真实户型图 |
| P1+ | 门弧投票抗墙中心线偏移 | `infer_door_swings` 横向/纵向容差搜索 | 偏移 8px 仍能确认 |
| P1+ | 混合描线（骨架 + 实心轮廓） | `scripts/sketch_trace.py` | demo 图 601 细线 + 85 轮廓 |
| P1+ | OCR 文本层（中英/可选） | `sketch_trace.ocr_texts` + `ocr_vision.swift` | demo 10 文本，含 BATH |
| P1+ | 2048 工作画布与分阶段计时 | `sketch_trace.load_ink` / stats | `working_size`、`ocr_ms`、`trace_ms` |
| P2 | 直跑楼梯生成 | `wall_plan.stair_calls` | 踏步线/箭头/文字数量与间距 |
| P2 | 直跑楼梯识别 | `image_to_calls.detect_stairs --stairs` | 8 步合成楼梯：步数/踏步/宽度 |

## 明确暂缓

| 项目 | 为什么暂缓 | 什么时候再做 |
| --- | --- | --- |
| 重写 C++/Go | 参考材料里 90% 是普通 OpenCV 管线；同架构重写只有 1.3–2×，Python 与 C++ 延迟差 13–20% | 需要单文件分发时，先考虑 PyInstaller，而不是重写 |
| 商用 SDK / Vectorizer.AI | 把语义藏进黑盒，增加授权与联网依赖 | 要做对外产品、且能承担许可与数据外发 |
| YOLO 语义层 | 会引入 torch/ultralytics 与模型下载，破坏当前 numpy+cv2+ezdxf 的轻依赖；预训练模型许可/精度需先验证 | 几何链路稳定、有真实图纸评测集、能接受模型体积时 |
| OCR 房间名/尺寸链 | 标定已有人工复核流程；OCR 误读会把整张图比例带偏 | 有稳定 OCR 环境，且输出必须经过"逐段复核 + 墙厚自检"闸门 |
| 交互式参数调优 GUI | agent 用参数扫描 + contact sheet 更省事，也便于留痕 | 有非 agent 的人工使用场景时 |

## 不要退化的约束

- 同一份 plan.json 的 calls / `.scr` / DXF 必须是同一套几何；新增后端也要走 `build_plan()`。
- 任何"猜"的结果（门窗类型、开向、楼梯）都要能落到 `warnings` 或 `review` 里。
- 示例 `assets/example-two-bedroom/plan.json` 必须保持 63 个图元、无警告——它是所有改动的地基。
