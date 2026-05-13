问题：Wikipedia Current Events 抓取的 summary 会把父级主题、子级链接和来源标记一起拼接，导致摘要不像真正的事件描述。
解决：调整 Wikipedia summary 提取逻辑，优先取含外部证据链接的最内层列表项，并去除主题前缀、嵌套列表文本和末尾来源括号。




# Plan: 将问题生成重构为「N 选 1」预测题
## Summary
把候选问题的硬性结构统一为 **N 选 1问题（设计的题目应该是选择题）**：每个问题必须给出有限、互斥、可判定的选项集合。模型先将事件结果拆解为 `Time + Subject + Action + Outcome`，再选择一个可预测维度出题：`Time`、`Subject` 或 `Outcome`。如果维度是连续或开放的，必须用方向、底线/截止日期、范围/分桶、幅度/差距等方式离散化。
## Key Changes
- 更新 `agent.py` prompt：
  - 明确禁止开放式事实问法：`What was the outcome...`、`What happened...`、`What resolution did...`。
  - 要求所有问题必须从 `prediction_date` 视角提出，并以 `As of YYYY-MM-DD, ...` 开头。
  - 要求每题只预测一个维度，不允许复合问题。
  - 要求模型按流程生成：事件拆解 -> 选择维度 -> 选择离散化方法 -> 写出 N 选 1问题。
  - 加入正反例，重点覆盖选举、利率、截止日期、范围分桶。

- agent 输出 schema无需改动


- 更新 `validation.py`保持轻量化，无需大的改动，因为目前这个不是重点。

## Question Design Rules
- 顶层结构：所有问题都是 N 选 1。
- 事件拆解：`Time + Subject + Action + Outcome`。
- 出题维度：
  - `time`：问何时发生。
  - `subject`：问谁 / 哪个主体。
  - `outcome`：问结果状态、程度、数量、方向、范围、是否达成。
- 连续维度必须离散化：
  - `direction`：上升 / 下降 / 不变。
  - `threshold_deadline`：是否达到底线，或是否在截止日期前发生。
  - `range_bucket`：落在哪个范围 / 分桶。
  - `magnitude_margin`：幅度 / 差距是多少。
- 示例目标格式：
  - `As of 2026-04-30, will Gaston Browne's ABLP win more than 10 of the 17 seats in the 2026 Antiguan general election?`
  - `options`: `["Yes", "No"]`
  - `answer_option`: `"Yes"`
  - `ground_truth`: `"ABLP won 15 of 17 seats."`
