# history_problem_builder

## 一、项目简介

`history_problem_builder` 旨在从历史新闻和公开数据源中收集事件，并进一步构造可复盘的历史预测问题。

项目的目标不是简单保存新闻摘要，而是形成一条从“事件发现”到“候选预测题生成”再到“人工复核”的工作流。

## 二、目前实现的步骤

第一步：
从公开网站和数据源中爬取历史事件，统一整理为结构化事件记录。当前事件数据会输出到 `data/event/`，主要包括 `events_*.jsonl`、`events_*.csv` 和被过滤事件记录。

第二步：
在现有事件爬取结果之上，新增一条独立的“历史预测问题生成流水线”：读取 `data/event/events_*.jsonl`，先用规则筛出适合构造问题的事件，再调用大模型 agent 生成候选问题，然后由验证层打风险标签，最后输出给人工复核的 CSV/JSONL。

## 三、项目结构

项目采用标准 `src` layout，核心代码位于 `src/history_question_builder/`。

```text
src/
  history_question_builder/
    event_crawler/                # 第一步：事件爬取与整理
      cli.py                      # 事件爬取命令行入口
      config.py                   # 运行配置、环境变量、默认目录
      filters.py                  # 事件过滤、去重和质量标记
      http_client.py              # HTTP session、headers、重试等网络封装
      models.py                   # CandidateEvent 等事件模型
      storage.py                  # events / dropped_events 的 JSONL、CSV 输出
      utils.py                    # 日期、URL、文本清洗等通用工具
      sources/                    # Wikipedia、IFES、FOMC、BLS 等来源爬虫

    question_asker/               # 第二步：历史预测问题生成
      agent.py                    # prompt 构建、mock provider、OpenAI-compatible provider
      models.py                   # 问题候选、拒绝记录、agent 返回结构
      pipeline.py                 # 问题生成流水线 CLI
      screening.py                # 发送给模型前的规则预筛选
      storage.py                  # question_candidates / rejected 输出
      validation.py               # 候选问题的机械验证和风险标签

data/
  event/                          # 事件爬取输出
  questions/                      # 问题生成输出

tests/
  test_question_pipeline.py       # 问题生成流水线的基础测试
```

## 四、运行方式

建议先在项目根目录安装 editable 包：

```bash
python -m pip install -e .
```

### 1. 爬取事件

```bash
crawl-events --start-date 2026-05-01 --end-date 2026-05-10 --output-dir data/event
```

也可以使用模块方式运行：

```bash
python -m history_question_builder.event_crawler.cli --start-date 2026-05-01 --end-date 2026-05-10 --output-dir data/event
```

常用参数：

```text
--start-date       开始日期，格式 YYYY-MM-DD
--end-date         结束日期，格式 YYYY-MM-DD
--sources          要爬取的数据源，多个来源用逗号分隔
--output-dir       处理后的事件输出目录
--raw-output-dir   原始抓取结果输出目录
```

### 2. 生成历史预测问题

使用 mock agent 跑通流程：

```bash
ask-questions --input data/event/events_2026-05-01_to_2026-05-10.jsonl --output-dir data/questions --agent-provider mock
```

调用真实大模型生成问题：

```bash
ask-questions --input data/event/events_2026-05-01_to_2026-05-10.jsonl --output-dir data/questions --agent-provider openai_compatible --model gpt-4.1-mini
```

也可以使用模块方式运行：

```bash
python -m history_question_builder.question_asker.pipeline --input data/event/events_2026-05-01_to_2026-05-10.jsonl --output-dir data/questions --agent-provider openai_compatible --model gpt-4.1-mini
```

调用真实模型前，需要在项目根目录 `.env` 中配置：

```env
OPENAI_API_KEY=你的 API key
QUESTION_AGENT_PROVIDER=openai_compatible
QUESTION_AGENT_MODEL=gpt-4.1-mini
QUESTION_AGENT_TEMPERATURE=0.2
```

常用参数：

```text
--input                 输入事件 JSONL
--output-dir            问题输出目录
--limit                 最多发送多少条事件给 agent
--agent-provider        mock 或 openai_compatible
--model                 使用真实模型时的模型名
--temperature           模型温度，默认 0.2
--max-retries           模型输出解析失败时的最大重试次数
--include-low-priority  是否包含低优先级事件，例如体育、娱乐、文化榜单类事件
```

## 五、待解决问题

1. 由于维基结构清晰，字段容易获得，所以目前爬虫只局限于维基。
2. 未调用真实api。
