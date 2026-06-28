# 第三轮:技术可行性 & 市场验证 — DevOps × AI Agent Hackathon 2026
> 四源交叉验证:Claude 文档核验 Workflow(对官方文档逐项核验)+ Codex(GPT-5.5,带源网搜)+ Gemini 3.1 Pro + 3.5 Flash
> 日期 2026-06-28 · 距提交 7/10 仅 ~12 天

---

## 摘要(两句话)
- **技术上可行,但"端到端全自治闭环"2 周内做不稳**——诚实降级:**核心可演示 = 确定性自动止血回滚**(最有冲击力、最低风险);PR+CI 用预置故障仓库 + 人工闸门;自动撤销回滚设为 stretch。
- **市场真实但早、且拥挤**:打"窗口外、可逆、动作层"差异化,ICP 双轨(楔子=Cloud Run 小团队 / 钱=企业 graduated autonomy)。

四源一致的**架构定论**:**确定性 FastAPI/ADK 状态机执行生产动作;Gemini 只做诊断 + 输出结构化决策——绝不让 LLM 自由动手碰生产。**

---

## Track A — 技术可行性

### 2 周内最稳技术栈(已核验)
| 层 | 选型 | 关键点 |
|---|---|---|
| 语言/框架 | **Python 3.11 + `google-adk~=1.0`** + FastAPI | ⚠️**必须锁 1.x**(见下) |
| 编排 | ADK `SequentialAgent`(triage→rollback→open_PR→verify)+ `LoopAgent`(带 `exit_loop` 工具轮询错误率直到归零/超时) | LlmAgent 做推理节点 |
| 决策 | Gemini 经典 `generateContent` + `generationConfig.responseSchema`(action 用 enum 锁状态集) | ⚠️别用新 Interactions API |
| 模型 | 决策用 Flash、补丁用 Pro(用最新 **Gemini 3.x**;你已在用 gemini-flash 按量、成本可忽略) | — |
| 执行(止血) | `google-cloud-run` `run_v2.ServicesClient.update_service` + `TrafficTarget(REVISION, percent=100)` + `.result()` | ⚠️钉死具体 revision,别用 LATEST |
| 信号 | Cloud Monitoring PromQL 对 `run.googleapis.com/request_count` 按 `response_code_class='5xx'` 算比率;归零 = 同 policy 的 incident `open→closed` | ⚠️加 synthetic probe |
| 触发 | Cloud Monitoring `webhook_tokenauth` 通知渠道 → Cloud Run `/alerts`(HTTPS+token+幂等) | 先回 200 再异步 |
| 修复 PR | **GitHub App**(非 PAT)+ Octokit(Contents RW / PR RW) | ⚠️PR 不触发 CI(见下) |
| CI/部署 | GitHub Actions 跑 PR;merge→main 走 Cloud Run continuous deployment;Actions 连 GCP 用 WIF 无密钥 | — |
| 持久化 | **DatabaseSessionService**(Cloud SQL/Postgres, asyncpg) | ⚠️别用默认 InMemory |
| 部署 | `adk deploy cloud_run`(`root_agent` 命名正确)+ **min-instances≥1** | 防冷启动错过告警 |
| 凭据 | Secret Manager + 最小 IAM(`run.developer`/`admin`、`monitoring.viewer`、`logging.viewer`、`secretmanager.secretAccessor`、`aiplatform.user`) | 绝不用 Owner |

### ⚠️ 五个会"静默坑死你"的已核实事实(CLI 不知道、Workflow 核验官方文档发现)
1. **ADK 2.0 是破坏性升级**(2026-05 stable,现 `pip install google-adk` 默认装 2.3.0):改成 graph-based runtime,`_run_async_impl()` 覆盖**被静默忽略**、1.x/2.0 session schema **不兼容**、95% 教程仍是 1.x 写法。→ **硬锁 `google-adk~=1.0`**,启动时断言版本号以 `1.` 开头 fail-fast。文档域名已从 `google.github.io/adk-docs` 迁到 **`adk.dev`**。
2. **GitHub App 开的 PR 默认不触发 `on:pull_request`**(GitHub 防递归机制)→ "走真 CI/CD"当场不跑、误以为接错。→ `ci.yml` 加 `on:push` 兜底,或用 `actions/create-github-app-token`;判 CI 绿读 **check-runs `conclusion=success`**。
3. **零流量陷阱**:错误率比率分母为 0 时,"归零"分不清"真修好"还是"没流量",可能在未修复时撤销回滚→二次事故。→ alert 加 `sum(rate(total)) > N` 前置 + 撤销前 agent **二次主动查指标 + synthetic `/healthz` probe** 确认目标 revision 正接流量。
4. **Cloud Run PATCH 是长跑 Operation 非即时**:不等完成就验证 = 流量没切完就误判。→ 客户端 `.result()` 阻塞等待;PATCH 带 `updateMask=traffic`。
5. **Gemini 结构化输出**:用经典 `generateContent`(v1beta)`responseSchema`,**别混用 2026-06 新主推的 Interactions API `response_format`**——会 400。

### 诚实降级 & 风险登记(高严重度)
- **端到端全自治闭环 2 周难稳**(跨 5+ 外部系统 + 不可逆生产动作 + 非确定性 LLM + 长时异步)。→ **核心 demo = 自动止血回滚(确定性、最有冲击力);PR 生成 + CI 用预置故障仓库 + 人工确认闸门;自动撤销回滚 = stretch(demo 用半自动:agent 给建议 + 一键确认)。**
- 告警风暴 → incident_id 幂等锁;回滚抖动 → cooldown;LLM 乱改代码 → demo 限定一类可定位的简单 bug;沙箱"本地绿远程红" → 沙箱命令==远程 CI 命令(同镜像同 lockfile)。
- **GCP 无现成学生 credits**(你的额度是 Azure/DO/Cloudflare;Gemini 按量 Tier 1)→ 开赛前一次性 enable 所有 API + 建计费账号;Cloud SQL 可临时降级 sqlite+aiosqlite。

### 最小切片(「延迟炸弹回滚」,按此顺序)
1. 部署能切好/坏行为的 demo 服务到 Cloud Run,发一个喷 5xx 的坏 revision,确保有 1 个已知健康旧 revision。
2. **写代码前**先手动 `gcloud run services update-traffic SVC --to-revisions <last-good>=100`,确认流量真切回、5xx 真降——**整个项目的地基,必须先红到绿一次**。
3. 回滚做成 Python FunctionTool(`run_v2`:list→选嫌疑 revision 的上一 Ready→update_service 设 100%→`.result()`)。
4. 建 PromQL alertPolicy(5xx 比率,duration=2×interval),手动触发坏 revision 确认 incident `open`。
5. 建 `webhook_tokenauth` 渠道指向 `/alerts`,端点先 200 再异步、校验 token、幂等。
6. 接最小自治闭环:`incident.state='open'`→止血→把"回滚到 rev-X 待撤销"写进持久化 session。**此时"独立告警(含窗口外)→自动止血"闭环成立 = 最有冲击力、最低风险的核心。**
7. 用 ADK 包装(SequentialAgent + Gemini 结构化决策),`adk deploy cloud_run`(min-instances≥1)。
8. 闭合:监听 `incident.state='closed'`,撤销前二次查指标(防零流量陷阱)→ `update-traffic --to-latest` 撤销。
9. (时间够再加)Gemini+GitHub App 开修复 PR + Actions 真 CI + 沙箱自纠错 + 人工闸门——**高风险 stretch,别让它阻塞核心止血闭环**。

---

## Track B — 用户 & 市场验证

### ICP 双轨(评委要听到你知道"楔子 ≠ 最终买家")
- **楔子(拿奖/落地)**:5–20 人、无专职 SRE、已在 Cloud Run 跑生产、半夜还被 paging 的小团队。集成面小、销售周期短、"工程师向工程师买、付费意愿高"、与必用的 Cloud Run 原生贴合——**12 天唯一现实 ICP**。定位:**"给小到养不起 SRE 的团队的 SRE-in-a-box"**。
- **真买家(钱在这,讲故事用)**:跑微服务/K8s、有 24/7 on-call、宕机每分钟烧钱的中大型团队。所有融资玩家都自上而下卖企业(Resolve 客户 = Coinbase/DoorDash/MongoDB/Salesforce/Zscaler)。

### 需求强度(量化、有源)
- 每周 **~2,000 告警仅 ~3% 可执行**;**44%** 因被忽略告警出过事故;**78% 出过"根本没有告警触发"的事故**(← 正中你"窗口外独立告警"卖点)。
- 宕机 **~$5,600/分钟**;**61%** 估 ≥$50k/小时;**~40%** 组织 on-call 倦怠 >25%。
- 瓶颈是"**协调税 / MTTR**"而非技术复杂度——正是自治"检测→回滚→证明→撤销"闭环要打的。

### 市场(报区间,别吹大)
- AI SRE 真实但早:Gartner *自治 SRE 使用率 <5%(2025)→ 85%(2029)*;AI agent 软件支出 *$86.4B→$206.5B(2026)→$376.3B(2027)*。
- **AIOps TAM 各家口径差异大($11B–$38B)**——报区间,明说"我们是其中快速增长的子集",别把整个 AIOps 当自己市场(评委一眼看穿)。

### 定价基准(已核实)+ 白空位
| 原型 | 代表 | 价 |
|---|---|---|
| A 按席位、AI 捆绑 | incident.io | Team ~$15–19、Pro ~$25/user/月;on-call 加购 +$10/+$20;全包 ~$31→$45 |
| B 按用量 | Datadog Bits AI SRE | ~$25–30/次有结论调查($500/月含20次);**需 Datadog 底座** |
| B 续 | PagerDuty Advance / AIOps | Advance $415/月起;AIOps $699/月起 |
| C 企业询价、价值定价 | **Resolve.ai / Cleric** | **均不公开价**(Resolve ~$1B 估值/~$4M ARR) |
→ **白空位:按"每次自动化解的事故 / 每次治愈的部署"透明计价**——贴近你"动作层/proof-of-recovery"的价值单元,填 Resolve/Cleric 不公开价的真空,且对没有 on-call 工程师可摊席位的小团队比按席位友好。

### 「とどける/实务」叙事 = 三个"真"
1. **真闭环(动作层 ≠ 诊断层)**:incident.io/Datadog Bits 多停在"检测→诊断→建议";你真动手:窗口外告警→切回健康 revision 止血→开真修复 PR 走真 CI/CD→Monitoring 证明错误率归零→撤销回滚。
2. **真可逆 = 真敢上生产**:止血是流量切换(切回已存在健康 revision),天然可逆、零数据迁移风险;代码修复走人工 gated PR + 真 CI。"**先证明 error-rate→0 再撤销回滚**" = 信任解锁点,把自治从赌博变成有证据可回退的工程动作(= 企业 graduated autonomy 首采形态)。
3. **真平台原生 = 护城河方向**:你 12 天赢不了巨头的"数据护城河",但能赢"**动作 + Cloud Run 原生**"——别人绑死自家遥测、停在平台内动作,你云原生、动作优先。

### 对 GCP 评委证明"実務で活きる"
演**窗口外**真事故(命中"78% 无告警")→ 摊开证据(左 Monitoring 曲线随回滚实时归零 / 右 Gemini PR 真 CI 跑绿,**全用真数据不 mock**)→ 强调可逆撤销 → ROI 锚点($5,600/min、MTTR 秒数换算省钱省唤醒)→ 主动点名对手差异化 → 必用栈讲成连贯 agentic 故事(Gemini 因果推理+生成修复、ADK 编排多步带闸门、Cloud Run 既是被治对象也是运行时、Logging/Monitoring 闭合证据环)。

### 市场风险
赛道拥挤热钱涌入(Resolve $150M+ / Traversal $48M / Neubird / Cleric / Parity + incident.io)→ 差异化必须是"具体闭环"非"又一个 AI SRE";最大结构威胁 = 拥遥测数据的可观测性巨头(Datadog Bits GA、PagerDuty SRE Agent ~2025-10 GA)→ 别正面拼诊断;ICP 错配要讲清双轨;自治信任风险 → 显式护栏;demo 真实性 → 全真数据。

---
> 原始四源 + 全量核验数据存档于 `research-archive/round3-feasibility-market/`。
