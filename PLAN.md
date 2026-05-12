# 历史预测问题生成与人工复核流水线实施方案

## Summary
在现有事件爬取结果之上，新增一条独立的“历史预测问题生成流水线”：读取 `data/event/events_*.jsonl`，先用规则筛出适合构造问题的事件，再调用大模型 agent 生成候选问题，然后由验证层打风险标签，最后输出给人工复核的 CSV/JSONL。


## Assumptions
- 第一版每个事件最多生成 1 道问题。
- 模型调用使用 OpenAI-compatible chat completion 形态，后续如切换正式 SDK 或 Responses API，只替换 `QuestionAgent` 实现，不改 pipeline。
- `mock` provider 用于本地测试和 CI，不代表真实题目质量。
- 真实模型调用需要用户自己配置 API key。
- 所有候选题都必须人工复核后才能进入最终题库。

## Key Changes

### 1. 新增问题生成流水线 CLI
新增独立命令，不改动现有 crawler 主流程：

```bash
python -m history_question_builder.question_asker.pipeline \
  --input data/event/events_2026-05-01_to_2026-05-09.jsonl \
  --output-dir data/questions \
  --limit 100 \
  --agent-provider openai_compatible \
  --model gpt-4.1-mini
```

保留离线调试模式：

```bash
python -m history_question_builder.question_asker.pipeline \
  --input data/event/events_2026-05-01_to_2026-05-09.jsonl \
  --output-dir data/questions \
  --limit 20 \
  --agent-provider mock
```

CLI 参数：
- `--input`: `data/event/events_*.jsonl` 输入文件。
- `--output-dir`: 问题输出目录，默认 `data/questions`。
- `--limit`: 最多处理多少条通过预筛选的事件。
- `--agent-provider`: `mock` 或 `openai_compatible`。
- `--model`: 模型名称，真实模型调用时必填或从环境变量读取。
- `--temperature`: 默认 `0.2`，降低随意发挥。
- `--max-retries`: 默认 `2`，模型输出解析失败时重试。
- `--include-low-priority`: 默认关闭，开启后允许体育、娱乐、文化榜单等补充领域进入模型。

环境变量：
- `OPENAI_API_KEY`: 真实模型调用所需 key。
- `OPENAI_BASE_URL`: OpenAI-compatible endpoint，默认 `https://api.openai.com/v1/chat/completions`。
- `QUESTION_AGENT_MODEL`: 默认模型名。
- `QUESTION_AGENT_PROVIDER`: 默认 provider。
- `QUESTION_AGENT_TEMPERATURE`: 默认温度。

### 2. 输出字段
候选问题文件只保留 13 个字段，面向人工复核：

```text
question_id
event_id
domain
event_name
question
prediction_date
ground_truth
resolution_source
risk_flags
event_summary
source_urls
review_status
review_notes
```

字段要求：
- `question_id`: 由 `event_id + question` 生成稳定哈希。
- `event_id`: 对应原始事件 ID。
- `domain`: 使用 PDF 中的领域值，优先归一化为 `politics`、`macro`、`public_risk`、`sports`、`entertainment`。
- `event_name`: 简洁事件名。
- `question`: 完整预测问题，默认开放式问题。
- `prediction_date`: 指“按这一天为止公开可见的信息来预测”，默认结果公开前一天。
- `ground_truth`: 最终答案。
- `resolution_source`: 证明答案的公开来源链接。也就是`evidence_url`
- `risk_flags`: 验证层风险标签，用分号分隔。
- `event_summary`: 压缩后的原始事件信息，格式为 `event_date | source | title | summary`。
- `source_urls`: 原始事件的 `source_url` 与 `evidence_urls` 合并字段，用分号分隔。
- `review_status`: 默认 `unreviewed`。
- `review_notes`: 默认空，留给人工复核填写。

拒绝记录单独输出，不混入候选表：

```text
event_id
reject_stage
reject_reason
event_summary
source_urls
```

`reject_stage` 可选：
- `pre_screen`: 规则预筛选阶段拒绝。
- `agent`: 模型判断不适合生成问题。
- `parse`: 模型输出无法解析或缺字段。
- `validation`: 预留阶段，第一版不使用自动淘汰。




### 3. Pipeline 数据流
整体流程：

```text
event JSONL
  -> load CandidateEvent
  -> pre-screen rules
  -> model agent generation
  -> parse and normalize JSON
  -> validation risk flags
  -> write candidate CSV/JSONL
  -> write rejected JSONL
```

预筛选规则：
- 高优先保留：选举、公投、组阁、FOMC、CPI、PPI、非农、GDP、PCE、央行利率、法院宣判、明确截止时间的停火/制裁/政策决定、极端天气路径或登陆结果、机场/服务恢复。
- 中优先保留：体育决赛、奖项、榜单、重要会议结果。
- 默认拒绝：袭击已经发生、抗议已经发生、逮捕已经发生、单纯伤亡数字、缺乏后续结果点、不可公开验证、只有传闻或截图来源。
- `source in {ifes_electionguide, fomc_calendar, bls_release_calendar, fred_release_calendar}` 默认高优先。
- `domain in {politics, macro, public_risk}` 默认优先。
- `domain in {other, conflict}` 只有命中明确未来判定点关键词时才进入模型。



### 4. 模型调用方式
新增一个 `QuestionAgent` 抽象接口：

```python
class QuestionAgent:
    def generate(self, event: CandidateEvent) -> AgentResult:
        ...
```

两个实现：
- `MockQuestionAgent`: 离线测试使用，不调用网络，返回固定 candidate/rejected 样例。
- `OpenAICompatibleQuestionAgent`: 真实生成使用，通过 `requests.post` 调用 OpenAI-compatible chat completion endpoint。

真实模型调用流程：
1. 将 `CandidateEvent` 转换为 compact JSON。
2. 用 `PromptBuilder` 生成 system prompt 和 user prompt。
3. POST 到模型 endpoint。
4. 要求模型返回纯 JSON。
5. 用 `json.loads` 解析。
6. 用 Pydantic 或等价校验检查字段。
7. 如果解析失败，带着错误信息重试一次。
8. 多次失败后写入 rejected，`reject_stage = parse`。

请求体形态：

```json
{
  "model": "gpt-4.1-mini",
  "temperature": 0.2,
  "messages": [
    {
      "role": "system",
      "content": "..."
    },
    {
      "role": "user",
      "content": "..."
    }
  ]
}
```

响应处理：
- 读取模型文本输出。
- 若输出包在 ```json 代码块中，先去掉代码块。
- 只接受 JSON object。
- 若包含 `reject_reason` 且没有 candidate 字段，视为 agent 拒绝。
- 若包含 candidate 字段但缺少必填字段，视为 parse reject。
- 不允许模型输出多个问题；第一版每个事件最多生成 1 道题。

### 5. Agent Prompt 设计
system prompt 要足够强，直接规定任务、合格标准、拒绝条件、输出格式。

建议 system prompt：

```text
你是一个历史预测问题构建助手。你的任务不是总结新闻，也不是把新闻机械改写成问句，而是判断一个历史事件是否可以构造成“在结果公开前可以提出的预测问题”。

你必须严格遵守以下标准。

一、只有同时满足这些条件，才可以生成问题：
1. 在 prediction_date 当天结束时，问题答案仍存在真实不确定性；
2. 后来已经出现明确 ground_truth；
3. question 的时间边界清楚；
4. question 的预测对象明确；
5. question 的判定标准明确；
6. ground_truth 可以通过公开来源验证，或至少能被可靠查证；
7. 问题具有复盘价值，不是随机琐事；
8. 问题不是单纯询问已经发生的事实。

二、遇到以下情况必须拒绝生成：
1. 新闻描述的是已经发生的即时事件，且没有自然的未来结果点；
2. 问题只能靠事后倒推，prediction_date 时其实已经知道答案；
3. 答案依赖传闻、截图、社交媒体碎片或不可追溯来源；
4. 问题含有模糊判断，例如“是否成功”“是否重大”“是否更好”“是否明显改善”；
5. 问题是纯数值碰运气，例如精确预测某股票某天收盘价；
6. 问题太私人化、圈内化或研究价值低；
7. 你无法确定 ground_truth 是否直接回答 question；
8. 你无法给出合理 resolution_source。

三、优先领域：
1. politics：选举、公投、组阁、停火、制裁、战争、外交会议；
2. macro：FOMC、央行利率、CPI、PPI、非农、PCE、GDP、通胀、汇率、黄金、原油、指数；
3. public_risk：极端天气、疫情周报、法院宣判、机场恢复、旅行警告、灾害响应；
4. sports：重要赛事冠军、晋级、决赛结果；
5. entertainment：奥斯卡、格莱美、票房榜、音乐榜、图书榜。

四、写题要求：
1. question 必须是完整预测问题；
2. question 默认是开放式问题，不需要写题型；
3. question 中必须有明确日期、事件日期或截止日期；
4. question 不要使用“可能”“大概”“明显”“重大”“成功”等模糊词；
5. prediction_date 默认填写结果公开前一天；
6. ground_truth 必须能直接回答 question；
7. resolution_source 优先使用官方机构、权威新闻源、赛事官网、选举机构、统计发布机构；
8. Wikipedia Current Events 可以作为发现来源，但不要优先作为最终答案来源。

五、输出要求：
你必须只输出一个 JSON object，不要输出 Markdown，不要输出解释文字，不要输出多个候选问题。

如果可以生成问题，输出：
{
  "event_name": "...",
  "domain": "politics | macro | public_risk | sports | entertainment",
  "question": "...",
  "prediction_date": "YYYY-MM-DD",
  "ground_truth": "...",
  "resolution_source": "..."
}

如果不可以生成问题，输出：
{
  "reject_reason": "..."
}
```

user prompt 模板：

```text
请根据下面的历史事件判断是否能构造一个合格的历史预测问题。

事件 JSON：
{
  "event_id": "...",
  "source": "...",
  "domain": "...",
  "event_date": "...",
  "title": "...",
  "summary": "...",
  "source_url": "...",
  "evidence_urls": [...]
}

请特别检查：
1. 这个事件是否有“结果公开前”的自然预测时间点；
2. prediction_date 应该是哪一天；
3. ground_truth 是否已经在事件文本或来源中明确出现；
4. question 是否会泄露事后答案；
5. resolution_source 是否足够可靠。

只输出 JSON object。
```

重试 prompt 追加内容：

```text
你上一次输出无法被解析为符合 schema 的 JSON。
错误原因：{parse_error}

请重新输出一个 JSON object。
不要使用 Markdown。
不要添加任何解释文字。
```

### 6. 验证层 Risk Flags
验证层只打标签，不删除候选题。

第一版风险标签：

```text
ambiguous_time_boundary
unclear_resolution_criteria
prediction_date_may_be_invalid
ground_truth_not_direct_answer
weak_or_missing_resolution_source
question_contains_vague_words
too_random_or_low_value
already_known_at_prediction_date
event_not_naturally_predictable
source_not_authoritative
needs_external_fact_check
```

规则：
- `question` 中没有明确日期、截止时间或事件时间，打 `ambiguous_time_boundary`。
- 出现“成功”“重大”“明显”“更好”“比较鹰派”“涨很多”等模糊词，打 `question_contains_vague_words`。
- `resolution_source` 为空，打 `weak_or_missing_resolution_source`。
- `resolution_source` 只指向 Wikipedia Current Events，打 `weak_or_missing_resolution_source`。
- `ground_truth` 为空或明显不能回答问题，打 `ground_truth_not_direct_answer`。
- `prediction_date >= event_date` 时，打 `prediction_date_may_be_invalid`，交给人工复核。
- 原始事件命中 attack、killed、injured、arrested、protest 等即时新闻词，且 question 没有后续判定点，打 `event_not_naturally_predictable`。
- 来源不是官方机构、权威新闻、统计机构、赛事官网或选举机构，打 `source_not_authoritative`。
- 任何需要人工打开链接确认的情况，打 `needs_external_fact_check`。

验证层的目的不是替代人工，而是把“哪里可能有问题”提前标出来。

### 7. 文件与模块设计
新增模块建议：

```text
src/history_question_builder/
  event_crawler/
  question_asker/
```

职责：
- `event_crawler/`: 事件采集子模块，包含数据源、过滤、存储和事件爬取 CLI。
- `question_asker/models.py`: `QuestionCandidate`、`RejectedQuestionEvent`、`AgentResult`。
- `question_asker/screening.py`: 规则预筛选，返回 selected/rejected。
- `question_asker/agent.py`: prompt 构建、mock agent、OpenAI-compatible agent、JSON 解析和重试。
- `question_asker/validation.py`: risk flag 规则。
- `question_asker/storage.py`: 候选 JSONL/CSV 和 rejected JSONL 写入。
- `question_asker/pipeline.py`: 问题生成 CLI 编排。
- 使用标准 src-layout；安装 editable 包后支持 `python -m history_question_builder.event_crawler.cli` 和 `python -m history_question_builder.question_asker.pipeline`。

不改动：
- 现有 crawler 数据源。
- 现有 `events_*.jsonl` / `events_*.csv` 输出格式。
- 现有 `CandidateEvent` 行为。

### 8. 输出文件
输出目录：

```text
data/questions/
```

候选问题：

```text
question_candidates_YYYY-MM-DD_to_YYYY-MM-DD.csv
question_candidates_YYYY-MM-DD_to_YYYY-MM-DD.jsonl
```

拒绝记录：

```text
rejected_question_candidates_YYYY-MM-DD_to_YYYY-MM-DD.jsonl
```

日期范围从输入文件名解析：
- 输入 `events_2026-05-01_to_2026-05-09.jsonl`
- 输出使用 `2026-05-01_to_2026-05-09`

如果输入文件名无法解析日期范围，则使用：
```text
question_candidates_custom.csv
question_candidates_custom.jsonl
rejected_question_candidates_custom.jsonl
```

### 9. Test Plan
新增最小测试目录：

```text
tests/
```

测试内容：
- 字段测试：candidate CSV/JSONL 只输出 13 个字段。
- rejected 测试：rejected JSONL 只输出 5 个字段。
- 预筛选测试：IFES election、FOMC、BLS release 被选中。
- 预筛选测试：attack、protest、arrested 类即时新闻默认 rejected。
- agent 解析测试：合法 candidate JSON 能转成 `QuestionCandidate`。
- agent 解析测试：合法 rejected JSON 能转成 rejected record。
- agent 解析测试：非 JSON、缺字段、坏日期不会导致 pipeline 崩溃。
- 验证测试：无时间边界打 `ambiguous_time_boundary`。
- 验证测试：模糊表达打 `question_contains_vague_words`。
- 验证测试：空来源打 `weak_or_missing_resolution_source`。
- 端到端测试：使用 mock agent 跑 20 条样本，确认生成 candidate/rejected 三个输出文件。

手动验收命令：

```bash
python -m history_question_builder.question_asker.pipeline \
  --input data/event/events_2026-05-01_to_2026-05-09.jsonl \
  --output-dir data/questions \
  --limit 20 \
  --agent-provider mock
```

真实模型验收命令：

```bash
python -m history_question_builder.question_asker.pipeline \
  --input data/event/events_2026-05-01_to_2026-05-09.jsonl \
  --output-dir data/questions \
  --limit 20 \
  --agent-provider openai_compatible \
  --model gpt-4.1-mini
```

验收标准：
- 命令能完成运行。
- candidate CSV 可直接人工复核。
- rejected JSONL 能说明每条未生成题目的原因。
- candidate 中所有记录都有 `review_status = unreviewed`。
- 验证层只添加 `risk_flags`，不自动删除候选题。
