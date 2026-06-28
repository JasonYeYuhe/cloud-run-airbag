# DevOps × AI Agent Hackathon 2026 — 四方调研汇总报告
> 调研方:Claude (Opus 4.8) · Codex (GPT-5.5) · Gemini 3.1 Pro · Gemini 3.5 Flash
> 汇总日期:2026-06-27 · 提交截止:**2026-07-10 23:59**(约 2 周)

---

## 0. 比赛硬事实
- 主办 Findy / 协赞 Google Cloud Japan。团队 **1–5 人**,需 **18+ 且居住日本**。
- **必用**:≥1 计算产品(**Cloud Run** / GKE / Cloud Functions / App Engine / GCE)+ ≥1 AI 产品(**Gemini API / ADK / Agent Builder / Gemini Enterprise Agent Platform** / Gemma / Imagen)。可选 Flutter / Firebase / Elasticsearch。
- 奖金 200 万日元:大奖 ¥50万×1、优秀 ¥30万×3、特别 ¥10万×6(共 10 组入围)。
- 时间线:报名 4/27–7/10;团队组建 6/7;Bootcamp 6/1–6/12;**提交 7/10**;入围 7/30;决赛 8/19(涩谷 Google,线下/混合)。
- 评分三轴:**つくる**=Agent 必要性+自治执行;**まわす**=真实 CI/CD 闭环+持续迭代;**とどける**=真部署、生产级、可规模化。

## 1. 要提交什么(四方共识)
1. **GitHub 公开仓库**(monorepo:`apps/web` + `apps/agent` + `infra` + `.github/workflows` + `docs`;README 首屏=问题/自治边界/Demo URL/架构图/复现步骤)。
2. **部署 URL ×2~3**:Agent 服务、可视化 Dashboard、被运维的"靶机" App,**全部跑在 Cloud Run**。
3. **Demo 视频 3–5 分钟**:制造故障 → Agent 自主接管 → CI/CD 真实跑 → 回滚/监控闭环 → 恢复绿灯。必须出现真实 GitHub Actions / Cloud Run / logs。
4. **架构文档 + DevOps 闭环文档 + 安全说明**(最小权限 SA、人工批准点、审计日志)。
5. **Pitch Deck 8–10 页**:第二页专门回答"**为什么必须是自治 Agent,而不是脚本/规则引擎**"。

## 2. 四方点子对照

| 模型 | 首推点子 | 其余点子 |
|---|---|---|
| **Claude** | 自治 SRE/值班 Agent(**在你真实 fleet 上 dogfood**) | PR 自动修复、自我进化 evals-loop、发布列车、Flaky 测试侦探 |
| **Codex** | **Release Guardian**(发布/验证/回滚) | Incident Commander、PR-to-Prod、Cost Autopilot、Synthetic QA、依赖升级、Runbook Executor |
| **Gemini 3.1 Pro** | **Auto-SRE 故障自愈** | FinOps 成本刺客、CI/CD 绿灯侠、SecOps Patcher、Chaos Agent、环境幽灵 |
| **Gemini 3.5 Flash** | **OpsPulse**(Sentry 自愈+闭环验证) | PerfTuner FinOps、SafeSchema DB迁移、Playwright Healer、SecGuard、ChaosBot |

### ⭐ 强收敛结论
**四个模型独立地都把"生产事故/发布闭环里能自主检测→修复/回滚→验证的自治 Agent"排为第一。** 这是这次黑客松最稳的夺冠形态——它天然把 つくる(自治)、まわす(CI/CD 闭环)、とどける(Cloud Run 真部署)三轴全占满,且 demo 极具冲击力。

## 3. 收敛后的最终方案 —— **"Release/Incident Self-Heal Agent"**

融合四方各自的最优洞见:

- **核心自治动作 = Cloud Run revision 回滚**(Codex/Gemini 洞见):回滚只需一条 `gcloud run services update-traffic` 命令,**100% 可靠**,作为自治闭环的地基。写代码修 Bug + 提 PR 是"加分档",务必带**自我纠错回路**(Flash:提 PR 前先在沙盒 `npm run build`/`test`,报错喂回 Gemini 重试)。若代码档不稳→**降级为 Auto-Rollback + Runbook**(Gemini Pro Plan B),闭环依然完整。
- **闭环**:监控(Sentry/Cloud Monitoring)→ 检测 → 诊断(Gemini 思考链)→ 执行(回滚 or PR)→ Cloud Logging/Monitoring 验证错误率清零 → 完成 or 再回滚。
- **GitOps + 安全边界**(Codex):最小权限 Service Account、危险动作设人工批准点、全程审计日志——这是"生产级"的硬证据。
- **杀手锏 Demo**(Gemini Pro):① 让 Agent **大声思考**——Dashboard 实时显示它调用的每个 ADK Tool + 中间决策(科技感深色大屏);② 现场故意搞挂一个服务,Agent 无人干预自愈;③ Dashboard 留一个**手动触发后门按钮**,保证答辩时随叫随到。
- **可信度加成**(Claude):把 Agent 指向**你自己真实的多 app fleet**(已接 Sentry/Datadog/NR),"真的在运维我自己的生产系统"是别人造不出来的 とどける 证据。
- **必用栈要显性**:ADK 做状态机(`idle→analyzing→acting→verifying→done/rollback`)、Gemini **Structured Outputs** 驱动状态流转、Cloud Run 托管、Cloud Logging/Monitoring 取证、Secret Manager 存 token。**别只调 Gemini API 就完事**(共识踩坑)。

### 架构(融合版)
```
触发源(Sentry / Cloud Monitoring 告警 webhook | Dashboard 手动按钮)
        │
        ▼
Cloud Run: Agent API ── ADK 状态机(Orchestrator + Diagnose/Act/Verify/Rollback 子 agent)
   ├─ Gemini 3.x(Structured Outputs 决策 + 代码诊断)
   ├─ Tools: GitHub API / Cloud Run Admin API(回滚)/ Cloud Logging / Cloud Monitoring
   ├─ Secret Manager(GitHub token, SA 凭据)
   └─ Firestore/Supabase(run 状态、决策日志、审计、release 历史)
        │
        ├─ 回滚:gcloud run update-traffic → 上一 stable revision
        ├─ 修复:开 PR → GitHub Actions CI → 构建镜像 → 部署 Cloud Run
        ▼
Cloud Run: 靶机 Demo App(/health + 可注入故障开关)
        │ 实时遥测
        ▼
Cloud Run: Next.js Dashboard(思考链 / Diff / CI 状态 / revision / 回滚·批准按钮 · Supabase Realtime 推送)
```

### 14 天冲刺(四方里程碑合并)
| 天 | 目标 |
|---|---|
| 1–2 | 锁 scope(单 repo+单服务);建 monorepo+架构图;Cloud Run 靶机 App(健康检查+故障开关) |
| 3–4 | GitHub Actions(test/build/push Artifact Registry/deploy);ADK Agent 骨架 + 工具封装(GitHub / Cloud Run Admin / Logging) |
| 5–7 | Agent 核心闭环:诊断 → **回滚**(先做这条,最稳)→ 验证;再加代码修复+自我纠错回路 |
| 8–9 | Next.js Dashboard(思考链可视化、revision、批准/回滚按钮);接入 Agent Platform 整理 Agent 定义 |
| 10–12 | 端到端 Demo①成功路径 / ②坏版本→检测→自动回滚→开 issue;修权限与稳定性;接真实 fleet(可选) |
| 13–14 | 录 Demo 视频、写 README/架构/安全文档、做 8–10 页 deck;**冻结功能只修 bug**,演练现场+备用录屏 |

## 4. 制胜策略(共识)
1. **死磕"自治执行"**:Agent 必须有写权限、有边界、有状态地**自己干活**(回滚/部署/开 PR),不是给建议的 chatbot。展示它失败后能**自动重试/回滚**=自治健壮性王牌。
2. **可观测的思考链**:后台默默干活对 demo 是灾难——必须把每步决策打到 Dashboard。
3. **真闭环 > 单次脚本**:`commit→CI→deploy→observe→decide→rollback/fix→learn`,且 GitHub Actions / Cloud Run revision / Monitoring 查询都**真跑给评委看**。
4. **深用 GCP**:Cloud Run + Logging + Monitoring + Secret Manager + ADK + Structured Outputs,而非只有 Gemini API。

## 5. 共识踩坑 & 风险对策
- ❌ 做成套壳 ChatGPT / ❌ CI/CD 只写在 README 没真跑 / ❌ 只展示成功路径没有失败回滚 / ❌ 权限过大无边界 / ❌ scope 过大(多云多 repo)导致 demo 不稳 / ❌ UI 很重但 Agent 很薄。
- **风险:Gemini 改代码引入新错 → CI 死循环**:提 PR 前本地编译/测试自纠错;实在不行降级为纯回滚+runbook。
- **风险:webhook/监控延迟,现场等不及**:Dashboard 留手动触发后门 + 预生成 release 历史 + 备用录屏。
- **风险:GCP IAM 权限耗时**:提前一天配好专用 SA(`run.admin` + `monitoring.viewer`),调不通就用 mock gcloud 返回标准 JSON。
- **Plan B 降级路线**:Release Guardian 做到 Day 7 仍不稳 → 砍成 Incident Commander(靶机+日志分析+自动回滚+开 issue),仍强契合三轴。

---
> 原始四份报告留存于 scratchpad:`out_codex.md` / `out_gemini_pro.md` / Flash 的 `hackathon_research_report.md`。
