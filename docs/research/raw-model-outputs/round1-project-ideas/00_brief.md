# 调研任务：DevOps × AI Agent Hackathon 2026（Google Cloud Japan 协赞 / Findy 主办）

你是一名资深的 AI Agent / DevOps 解决方案架构师。请基于以下**已核实事实**，输出一份**具体、可排序、可执行**的参赛调研报告。用**中文**回答。不要复述事实本身，直接给结论和方案。

## 已核实事实（无需再查）
- 主办：Findy 株式会社；协赞：Google Cloud Japan。地点：日本（决赛在 Google 涩谷 Stream 办公室线下）。
- 主题：「つくる、まわす、とどける」(Make / Run / Deliver)——做出 AI Agent、用 DevOps/CI-CD 持续迭代、部署到 Google Cloud 真正交付给用户。
- 三大评审重点：① つくる=Agent 的**必要性 + 自治执行设计**（不是创意新颖度，而是"为什么必须自治、自治得好不好"，要能自己执行任务而不只是给答案）；② まわす=有真实的 CI/CD DevOps 闭环、能持续迭代改进；③ とどける=部署到 Google Cloud、生产级质量、能规模化交付。还有"评委特别奖"奖励出色概念/技术。
- 必用技术栈：**Gemini**（核心模型，最新 Gemini 3.x）、**ADK (Agent Development Kit)**、**Gemini Enterprise Agent Platform**、**Google Cloud**（Cloud Run、Gemini API 等）。
- 奖金池：200 万日元。
- 时间线（今天是 2026-06-27）：报名 4/27–7/10 23:59；团队组建 6/7；Agentic AI Bootcamp 6/1–6/12；**项目提交截止 7/10（周五）**；入围 10 组公布 7/30；决赛 8/19。→ **距提交只剩约 2 周。**

## 参赛者画像（据此定制方案）
- 独立/小团队全栈开发者，出货快，已上线多款 iOS + Web 产品。
- 技术栈熟：TypeScript/Node、React/Next、Supabase、Vercel、Cloudflare Workers、Sentry/Datadog/New Relic 可观测、已用过 Google Gemini API。
- 有 GitHub Student Pack 各种额度；能快速搭 CI/CD（GitHub Actions）。
- 偏好：2 周内能做出能 demo、能部署、有真实闭环的项目，而不是 PPT 概念。

## 请输出（严格按此结构）
1. **要提交什么**（推断 deliverable 清单）：GitHub 仓库？部署 URL？Demo 视频？架构文档？演示文稿？分别给出建议规格。
2. **6–8 个具体项目点子**，按"2 周可行性 × 契合评审重点"排序。每个点子给：
   - 一句话定位 + 目标用户
   - Agent **自治**做什么（强调自主执行，不只问答）——这是评分核心
   - 用到的 Google Cloud / ADK 组件
   - 「まわす」体现在哪（它自己参与/触发的 CI-CD 或迭代闭环是什么）
   - 「とどける」如何部署交付
   - 为什么评委会喜欢 / 差异化亮点
   - 2 周内可行性（高/中/低）与最小可演示范围(MVP)
3. **最推荐的 1 个点子**：画出技术架构（组件、数据流、用到的 GCP 服务），列出 2 周冲刺里程碑（按天/阶段）。
4. **制胜策略**：在"自治 + DevOps 闭环 + 真部署"三项上如何最大化拿分；常见踩坑。
5. **风险与备选**。

要求：点子要"DevOps 味道"足（agent 真的去操作 CI/CD、基础设施、监控、发布、回滚、代码评审、事故响应等），避免泛泛的聊天机器人。具体、可落地、可在 2 周内做出 demo。
