# 计划：关键词增强版 Pre-Screen

## 核心观点

`screening.py` 不应该放弃关键词。关键词的优点是成本低、可解释、容易针对样本调试；真正的问题是旧逻辑把少量关键词当作单点判断，太容易误杀或误放。

现在采用的方向是：

- 继续以关键词为核心；
- 把关键词分成不同信号组；
- 用组合规则判断“是否真的有预测结构”；
- 用轻量分值排序优先级；
- 对高误判文本加明确反向护栏。

一句话：不是“关键词 vs AI”，而是先用更细的关键词规则把明显不适合的事件挡住，把结构强的事件送进 agent。

## 已实现的关键词信号组

位置：`src/history_question_builder/question_asker/screening.py`

- `TOPIC_VALUE_PATTERNS`：主题价值信号，例如 election、parliament、court、FOMC、summit、cup、derby、airport、relations、agreement。
- `FORECAST_SETUP_PATTERNS`：预测设置/流程信号，例如 scheduled、will start、takes effect、vote、election、final、ruling、deadline。
- `RESOLUTION_PATTERNS`：可验证结果信号，例如 won、elected、sworn in、passed、approved、ruled、lifted、reported、held、votes、seats、percent。
- `IMMEDIATE_NEWS_PATTERNS`：即时新闻负向信号，例如 killed、died、attack、airstrike、arrest、protest、explosion、fire。
- `REWRITE_RISK_PATTERNS`：新闻改写风险信号，例如 says、announces、reports、discusses、expected、may resume。
- `NUMERIC_RESULT_PATTERNS`：数值结果信号，例如 percent、rate、votes、seats、score、margin、drop、increase、2-0/2–0。
- `LOW_PRIORITY_TOPIC_PATTERNS`：默认低优先级主题，例如 awards、box office、ranking、sports、horse racing、football。

每个集合已在代码中加中文注释，说明它代表什么、为什么不能单独决定放行。

## 已实现的组合规则

新增 `ScreenSignals`，把每个事件拆成一组布尔信号和分值。重点组合包括：

- `sports_result`：体育/决赛词 + 胜负或比分。
- `court_result`：法院/司法词 + 裁决、判刑、暂停、非法等结果词。
- `vote_result`：议会/投票/不信任案 + passed/failed/approved/ousted/successful 等结果词。
- `election_result`：选举词 + won/elected/seats/concedes/claims victory，另补 `party + concedes defeat/claims victory`。
- `official_numeric_result`：官方统计主题 + reports/reported/estimated + 数值结果。
- `diplomatic_result`：外交/关系/协议主题 + agree to restore relations/sign agreement 等正式状态变化。
- `olympic_policy_result`：奥运/运动员主题 + lifts restrictions/neutral participation/suspension。
- `summit_attendance_result`：summit/conference + held/leaders from/participating/countries。
- `office_transition_result`：president/prime minister/first minister/minister/speaker 等公职 + sworn in/elected/appointed/ousted/resigns。

现在 `other` 和 `conflict` 领域不会被一刀切拒绝；只要命中强组合，也可以进入 agent。

## 已实现的反向护栏

这两类文本分数可能很高，但实际上没有可用结果，因此显式拒绝：

- `election_activity_without_result`：例如 “Voters elect six mayors and council seats”，这是投票/席位安排描述，不是结果。
- `debate_without_decision_result`：例如 “parliament debates a no-confidence motion”，只是辩论，不是表决结果。

同时保留：

- `immediate_news_without_forecast_setup`：伤亡、袭击、抗议等即时新闻，除非有强结构结果，否则拒绝。
- `rewrite_risk_too_high`：普通声明/媒体说法，没有结果或流程支撑时拒绝。
- `missing_resolution_signal`：没有可验证结果信号时拒绝。

## 当前打分规则

- 高质量结构化来源：`+4`
- 命中主题价值信号：`+2`
- 命中预测设置/流程信号：`+3`
- 命中可验证结果信号：`+3`
- 命中数值结果信号：`+2`
- 主领域 politics/macro/public_risk：`+1`
- 官方数值结果组合：额外 `+2`
- 外交/奥运/峰会组合：额外 `+2`
- 公职变动组合：额外 `+1`
- 低优先级主题但 domain 不是 sports/entertainment：`-1`
- 即时新闻负向信号：`-3`
- 新闻改写风险信号：`-2`

放行不是只看分数，还必须命中结构组合。这样可以避免 `reports`、`begins`、`election` 这类单词把普通新闻误放进去。

## 本轮样本迭代结果

测试样本：`data/event/events_2026-05-01_to_2026-05-10.jsonl`

最终 by-screen 结果：

- 总事件数：`320`
- pre-screen 放行：`46`
- pre-screen 拒绝：`274`

放行原因：

- `high_score_keyword_combo`：`42`
- `borderline_keyword_combo`：`4`

拒绝原因：

- `immediate_news_without_forecast_setup`：`151`
- `rewrite_risk_too_high`：`54`
- `missing_resolution_signal`：`39`
- `domain_requires_structured_signal:conflict`：`12`
- `domain_requires_structured_signal:other`：`10`
- `election_activity_without_result`：`3`
- `low_score_no_predictable_result`：`3`
- `debate_without_decision_result`：`2`

本轮修掉的代表性问题：

- 召回：Polish Cup、Kentucky Derby、Dubai passenger traffic、Romanian no-confidence vote、court rulings、Cambodia/Thailand restore relations、IOC lifts restrictions、European Political Community Summit、sign agreement、office transition。
- 拒绝：`Voters elect six mayors...`、`parliament debates a no-confidence motion...`、`may resume talks next week`、UFO files release、伤亡/袭击/抗议类即时新闻。

## 测试方式

已补充单元测试：`tests/test_question_pipeline.py`

覆盖场景：

- 强结构组合应该放行：体育决赛、法院裁决、正式投票、恢复外交关系、签署协议、峰会出席、公职变动、不信任案成功、党派承认败选/宣称胜利。
- 高误判场景应该拒绝：即时新闻、选民投票活动但无结果、只辩论不信任案但无表决、可能恢复会谈、普通文件释放新闻。

验证命令：

```bash
python -m unittest tests.test_question_pipeline
```

当前结果：`17 tests OK`。

## 后续可选优化

- 增加一个 `--screen-report` 参数，把每个事件的 `score`、命中的关键词组、组合信号和拒绝原因输出成 CSV，方便人工审阅。
- 对同一 summary 的重复 Wikipedia 标题，在 by-screen 报告阶段单独标出，避免把重复标题误看成误判。
- 后续如果样本扩大，可以按误判样本继续补小型组合规则，而不是把单个关键词门槛调得过松。
