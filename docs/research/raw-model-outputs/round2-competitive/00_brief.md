# 竞品 & 差异化调研任务（DevOps × AI Agent Hackathon 2026）

背景：我们要做一个 **"自治发布 / 事故自愈" DevOps AI Agent**（检测生产故障 → 自主回滚 Cloud Run 版本 / 开修复 PR 走真实 CI/CD → 用监控验证错误率清零 → Dashboard 可视化思考链）。必用 **Gemini + ADK + Cloud Run**。团队小、只有 ~2 周。今天 2026-06-27。

请从你的知识出发做**竞品盘点 + 差异化**调研，**尽量点名具体产品**。用**中文**，具体、可落地。

## 输出结构
1. **市面已有竞品盘点**，按类分（每个产品给：厂商 / 它实际能自治到什么程度【只给建议 vs 能执行动作 vs 闭环验证+自动回滚】/ 强项 / 弱点或没做到的）：
   - (a) AI SRE / 事故响应 / 自动修复（如 Cleric、Resolve.ai、Traversal、Parity、Neubird、Flip AI、Shoreline、Robusta/HolmesGPT、Causely、BigPanda、Datadog Bits AI、PagerDuty Advance、incident.io AI…）
   - (b) 自治编码 / PR 自动修复 / AI 代码评审（Devin、Google Jules、GitHub Copilot coding agent / Autofix、Sweep、CodeRabbit、Qodo、Greptile、Graphite、Ellipsis、Korbit、Charlie Labs、Codegen…）
   - (c) CI/CD 自愈 / flaky 测试 / 合并自动化（Trunk、Aviator、Mergify、BuildPulse、Harness AI、GitLab Duo…）
   - (d) FinOps / 自治云成本+可靠性优化（Sedai、Cast AI、Antimetal、Pump、Vantage、Densify…）
   - (e) **Google 原生**（必须重点）：Gemini Cloud Assist + Cloud Assist Investigations、Gemini Code Assist agents、Jules、ADK / Agent Garden 样例、Vertex AI Agent Engine、Antigravity——它们已经能做什么？
2. **可学习的最佳实践模式**（别人做得好的）。
3. **市场空白 / 还没人做好的点**，尤其：修复后"自动验证+自动回滚"闭环、提交前"自我纠错"、GitOps 审计与安全边界、真正端到端无人值守、跨信号关联（Sentry+部署+指标）。
4. **给我们 5–8 个差异化/特殊卖点**，按"新颖 × 2周可行 × 对 Google 评委的吸引力"排序。**特别要和 Google 自家的 Gemini Cloud Assist / Jules 明确区分**——不能做一个评委自己已经有的东西。

要诚实区分"营销话术"和"真能做到的"。
