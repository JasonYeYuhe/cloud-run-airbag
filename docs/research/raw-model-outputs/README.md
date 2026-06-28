# 研究存档 — DevOps × AI Agent Hackathon 2026

四模型多轮调研的原始产物存档。汇总结论见上级目录的两份报告。

## 汇总报告(已收敛结论,看这三份即可)
- `../hackathon_research_consolidated.md` — 第 1 轮:要提交什么 + 项目点子 + 14 天计划(收敛=自治"发布/事故自愈" Agent)
- `../hackathon_competitive_analysis.md` — 第 2 轮:42 竞品全景 + 可学习点 + 7 个市场空白 + 6 个排序差异化卖点
- `../hackathon_feasibility_and_market.md` — 第 3 轮:技术可行性(最稳技术栈 + 5 个已核实坑 + 诚实降级 + 最小切片)+ 市场验证(ICP 双轨 + 量化需求 + 定价基准 + 实务叙事)

## 原始产物
### round1-project-ideas/(第 1 轮:项目方向)
- `00_brief.md` — 下发给各模型的统一调研 brief
- `codex_gpt5.5.md` — Codex(GPT-5.5)
- `gemini-3.1-pro.md` — Gemini 3.1 Pro(经 Antigravity `agy`)
- `gemini-3.5-flash_full-report.md` — Gemini 3.5 Flash 完整报告
- `gemini-3.5-flash_stdout.md` — Flash 的 stdout 摘要

### round2-competitive/(第 2 轮:竞品 & 差异化)
- `00_brief.md` — 竞品调研 brief
- `codex_gpt5.5.md` — Codex(带引用的实时网搜)
- `gemini-3.1-pro.md` / `gemini-3.5-flash.md` — 两个 Gemini
- `claude-websweep_42-competitors.json` — Claude 多 agent 实时网搜全量结果(6 路搜索 → 去重审计 42 竞品 → 3 视角差异化),JSON

### round3-feasibility-market/(第 3 轮:技术可行性 & 市场验证)
- `00_brief.md` — 第三轮 brief(Track A 技术 + Track B 市场)
- `codex_gpt5.5.md` — Codex(技术最准:正确 ADK/genai SDK + synthetic-probe 洞见 + 带源市场数据)
- `gemini-3.1-pro.md` / `gemini-3.5-flash.md` — 两个 Gemini(注:两者 SDK 名都写错,以 Codex/Workflow 为准)
- `claude-docverify_feasibility-market.json` — Claude 文档核验 Workflow 全量(逐项核验官方文档,发现 ADK 2.0 破坏性升级等关键坑),JSON

## 调研方法
统一 brief 下发 4 个模型独立作答再交叉验证:Claude(主 + 多 agent workflow 网搜)、Codex `codex exec`、Gemini 3.1 Pro / 3.5 Flash(经 Antigravity `agy --print`)。详见记忆 `multi-model-cli-delegation`。
