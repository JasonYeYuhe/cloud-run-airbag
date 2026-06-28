这是一份针对您的「发布/事故自愈」DevOps Agent 项目的第三轮调研与落地规划报告。考虑到距离 7 月 10 日提交仅剩不到 2 周，本报告的核心原则是：**砍掉花哨功能，用最确定的技术栈保核心闭环，确保在评委面前能够演示出「真金白银」的实务价值。**

### Track A — 技术可行性 & 落地路径

**核心策略：确定性止血（脚本/API） + 概率性修复（大模型），两者严格解耦。**

#### 1. ADK (Agent Development Kit) 技术栈与架构
*   **首选语言：Python**。
    *   **理由**：AI 生态最完善。无论是对接 Vertex AI、LangGraph 做状态机，还是用 `PyGithub` 操作 PR，Python 的现成代码最多，遇到 Bug 解决最快（12天极限开发，不建议用 Java 或 TS 踩坑）。
*   **状态机 / Workflow 定义**：不要搞复杂的 Multi-agent 黑盒交互，使用一个显式的顺序状态机（Sequential Workflow）。最稳的做法是用 FastAPI 结合硬编码的状态扭转：`收到告警 -> [状态1: API 执行回滚] -> [状态2: 查日志] -> [状态3: LlmAgent 修复] -> [状态4: 提 PR]`。
*   **部署**：使用 **Vertex AI Reasoning Engine**（本质是托管的 Cloud Run），或直接用 Docker 包装 FastAPI 部署到普通的 **Cloud Run**。
*   **最小骨架代码 (FastAPI + 伪代码状态机)**：
```python
from fastapi import FastAPI, Request
import vertexai
from vertexai.generative_models import GenerativeModel

app = FastAPI()

@app.post("/webhook/alert")
async def handle_alert(request: Request):
    payload = await request.json()
    service_name = payload['incident']['resource_name']
    
    # State 1: 止血 (Deterministic - 确定性规则)
    rollback_status = rollback_to_previous_healthy(service_name)
    
    # State 2: 诊断与修复 (Probabilistic - 概率性探索)
    logs = fetch_recent_error_logs(service_name)
    fix_code = gemini_agent_propose_fix(logs, get_source_code())
    
    # State 3: 开 PR 交给真 CI 校验
    pr_url = create_github_pr(fix_code)
    
    return {"status": "mitigated", "pr": pr_url}
```

#### 2. Cloud Run 回滚 (确切命令与 API)
*   **找准“引入故障的 Revision”与“上一健康 Revision”**：
    默认最新 revision 接受 100% 流量。倒数第二个 revision 即可认为是健康的兜底版本。
    *   *命令行排查*：`gcloud run revisions list --service [SERVICE] --region [REGION] --sort-by=~metadata.creationTimestamp --limit=2`
*   **切流量 (回滚) API**：
    *   *命令行*：`gcloud run services update-traffic [SERVICE] --to-revisions=[PREV_REVISION]=100 --region [REGION]`
    *   *Python SDK (Cloud Run Admin API v2)*：
```python
from google.cloud import run_v2

def rollback(project, location, service_id, target_revision):
    client = run_v2.ServicesClient()
    name = f"projects/{project}/locations/{location}/services/{service_id}"
    
    # 设置 100% 流量给旧版本
    traffic_target = run_v2.TrafficTarget(
        revision=target_revision,
        percent=100
    )
    request = run_v2.UpdateServiceRequest(
        service=run_v2.Service(name=name, traffic=[traffic_target])
    )
    # 异步操作，需 wait()
    operation = client.update_service(request=request)
    return operation.result() 
```

#### 3. Gemini 决策驱动 (Structured Outputs)
为了防止 LLM 废话连篇导致正则解析失败，**必须**使用 Structured Outputs (Response Schema)。
```python
import vertexai.generative_models as generative_models

schema = generative_models.ResponseSchema(
    type=generative_models.Type.OBJECT,
    properties={
        "root_cause": generative_models.ResponseSchema(type=generative_models.Type.STRING),
        "file_to_modify": generative_models.ResponseSchema(type=generative_models.Type.STRING),
        "modified_code": generative_models.ResponseSchema(type=generative_models.Type.STRING),
    },
    required=["root_cause", "file_to_modify", "modified_code"]
)

model = generative_models.GenerativeModel("gemini-1.5-pro-001")
response = model.generate_content(
    f"Crash logs: {logs}\nSource Code: {code}\nFix the bug.",
    generation_config=generative_models.GenerationConfig(
        response_mime_type="application/json",
        response_schema=schema
    )
)
```

#### 4. 监控验证 & 触发配置
*   **指标判定 (MQL)**：在 Cloud Monitoring 查 5xx 错误。
    `fetch cloud_run_revision | metric 'run.googleapis.com/request_count' | filter (metric.response_code_class == '5xx') | align rate(1m) | every 1m`
*   **告警与 Webhook 触发**：
    1. 在 Cloud Monitoring 中创建 Alert Policy，条件设为 5xx 比例超出阈值。
    2. 创建 Notification Channel，类型选 **Webhook**，URL 指向你 Agent 所在 Cloud Run 的 `/webhook/alert` 端点。
*   **判定归零**：Agent PR 被合并（监听 GitHub Webhook）后，启动一个后台轮询线程（或通过 Cloud Scheduler 触发），连续 3 分钟拉取 Monitoring API 确认 5xx 率为 0 后，再执行 API 解除流量锁定（恢复 normal 部署流程）。

#### 5. 修复 PR + CI 联动
*   用 `PyGithub` 操作。获取源码 -> `repo.create_git_ref()` 切新分支 -> 写入 Gemini 给的代码 -> `repo.create_pull()` 开 PR。
*   如果在 Hackathon 想要极限秀操作：监听 GitHub Actions CI 失败的 webhook，如果 CI 没过，让 Agent 抓取 CI 报错追加到 Prompt 里发起**二次修复**（Self-Correction Loop），但一定要设 max_retries=2 防止死循环。

#### 6. IAM 与安全隔离
给 Agent 对应的 Service Account 配置最小权限，千万别给 Owner：
*   `roles/run.admin` (切流量)
*   `roles/monitoring.viewer` (读指标告警)
*   `roles/logging.viewer` (读报错堆栈)
*   `roles/aiplatform.user` (调用 Gemini)
*   `roles/secretmanager.secretAccessor` (获取 GitHub Token)

#### 7. 高风险待验证点 & 最小技术切片顺序
**2周内最稳选型结论**：**Python + FastAPI Webhook + Vertex AI Schema + PyGithub**。不要在这时候去啃复杂的多 Agent 编排框架底座。
**验证顺序（「延迟炸弹」打法）：**
1. **Day 1-2**：手动部署一个必报错 (抛 500) 的服务，配置 Monitoring 告警，确保 Agent 的 Webhook 能够**稳定收到 payload 并解析出 Service Name**。
2. **Day 3-4 (高亮环节)**：Webhook 收到请求后，用 Python 调用 API 成功把流量切回上一版本。**这一步只要做出来，比赛就有了保底的震撼效果。**
3. **Day 5-7**：拉取源码，喂给 Gemini，解析 JSON 输出并开 PR。
*   **已知坑 (警惕)**：
    *   **并发告警轰炸**：故障时告警会狂发，你的 Agent 可能会同时切 100 次流量开 100 个 PR。**对策**：必须在 Agent 内存或 Redis 做「防抖/锁」，处理过一次的 Incident ID 短期内忽略。
    *   **LLM 瞎改代码**：要求输出整文件容易丢失逻辑。**对策**：演示时构造特定的简单 Bug（比如硬编码的空指针），确保 Gemini 能 100% 修复通过 CI。

---

### Track B — 用户 & 市场验证

#### 1. ICP / 目标用户与痛点
*   **ICP**：使用 Cloud Run/Serverless 的中小型研发团队（5-50人）或独立开发者。他们没有专职 SRE 部门，推崇 "You build it, you run it"。
*   **最痛场景**：**凌晨 3 点的 PagerDuty 电话**。多数情况下，深夜被叫醒的工程师头脑不清醒，唯一能做的安全操作就是无脑执行 Rollback，然后继续睡，第二天再排查。Agent 就是代替人类完成这个“起夜点回滚”的脏活儿。

#### 2. 真实需求强度 + 付费意愿与定价
*   **需求极高**：解决的是“睡眠剥夺”与“MTTR 业务中断”的双重痛点。
*   **定价参考与策略**：
    *   传统如 PagerDuty (\$20-\$40/人/月) 只管“喊人”。
    *   你的产品主打“免喊人”，可采用 **SaaS 基础费 (\$49/月) + 按成功避险次数计费 (Usage-based, 如 \$5/次有效回滚)**。让客户感觉“只为避免的宕机时长买单”。

#### 3. AI SRE / 自治修复市场趋势
*   **从 Copilot 到 Closed-loop**：过去（如 Datadog Bits）主打 "Explain this error"（解释日志），依然需要人类介入。现在市场的浪潮是 "Auto-Remediation"（闭环自愈），也就是能直接动手（如 Devin, SWE-Agent 概念向运维延伸）。这是一个百亿美元级别的蓝海市场。

#### 4. 「実務/实务」叙事：为什么这不是 Demo
这是征服评委的关键。传统的 AI 运维容易被视为“不敢上生产的玩具”。
*   **叙事策略：信任梯度 (Trust Gradient) 设计**。
    向评委强调：“我们深知不能把线上流量的生杀大权交给存在幻觉的 LLM。因此，我们产品的**第一步止血（Rollback）是 100% 确定性的规则驱动**，绝对不会搞砸线上；而**第二步的 AI 修复被关在了沙盒里**，它生成的代码必须走标准的 CI/CD 流程测试，由真实世界（编译、单测）来校验 LLM，最后还是由人类合并。这种『线上确定性兜底 + 线下探索性修复』才是企业真敢用的架构。”

#### 5. 对 GCP 评委：如何展示实务价值
*   **高度赞美 GCP 生态**：强调这个方案之所以能如此轻量落地，全靠 Cloud Run 极其优秀的 Revision 流量拆分能力（比 K8s 回滚快得多且无痛），以及 Vertex AI 强大的结构化输出能力。
*   **演示设计 (Wow Factor)**：
    1. 左右分屏：左侧是业务页面的定时探测，右侧是 Agent 的可视化日志/Slack。
    2. 发布带 Bug 的版本，左侧瞬间变红（500 报错）。
    3. 几秒钟后，右侧自动打印：“检测到异常告警，触发安全阈值 -> 执行 Cloud Run 秒级回滚”。
    4. **左侧页面自动变绿恢复正常。** (视觉冲击力最强的一刻)
    5. Agent 继续打印：“已拉取崩溃日志，Gemini 正在分析 -> 已定位空指针 -> 修复 PR #42 已提交，正在运行 CI”。
    6. **结语金句**：“我们不是在做一个帮人写代码的玩具，我们是在利用 GCP 构建一个能让开发者安心睡整觉的自治底座。”
