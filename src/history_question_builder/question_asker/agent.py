from __future__ import annotations

"""Question-generation agents and prompt construction."""

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import requests
from pydantic import ValidationError

from ..event_crawler.models import CandidateEvent
from .models import AgentCandidatePayload, AgentResult, AgentRejectedPayload


SYSTEM_PROMPT = """你是一个历史预测问题构建助手。你的任务不是总结新闻，也不是把新闻机械改写成问句，而是判断一个历史事件是否可以构造成“在结果公开前可以提出的预测问题”。

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
"""


@dataclass(frozen=True)
class PromptBuilder:
    """Build prompts for the question-generation agent."""

    def build_messages(
        self, event: CandidateEvent, *, parse_error: str | None = None
    ) -> list[dict[str, str]]:
        user_prompt = (
            "请根据下面的历史事件判断是否能构造一个合格的历史预测问题。\n\n"
            "事件 JSON：\n"
            f"{json.dumps(_event_payload(event), ensure_ascii=False, indent=2)}\n\n"
            "请特别检查：\n"
            "1. 这个事件是否有“结果公开前”的自然预测时间点；\n"
            "2. prediction_date 应该是哪一天；\n"
            "3. ground_truth 是否已经在事件文本或来源中明确出现；\n"
            "4. question 是否会泄露事后答案；\n"
            "5. resolution_source 是否足够可靠。\n\n"
            "只输出 JSON object。"
        )
        if parse_error:
            user_prompt += (
                "\n\n你上一次输出无法被解析为符合 schema 的 JSON。\n"
                f"错误原因：{parse_error}\n\n"
                "请重新输出一个 JSON object。\n"
                "不要使用 Markdown。\n"
                "不要添加任何解释文字。"
            )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]


class QuestionAgent(ABC):
    """Abstract generation agent."""

    @abstractmethod
    def generate(self, event: CandidateEvent) -> AgentResult:
        """Generate or reject one question candidate for an event."""


class MockQuestionAgent(QuestionAgent):
    """Deterministic local agent for tests and dry runs."""

    def generate(self, event: CandidateEvent) -> AgentResult:
        text = " ".join([event.source, event.domain, event.title, event.summary]).lower()
        if not any(
            token in text
            for token in [
                "election",
                "fomc",
                "interest rate",
                "cpi",
                "gdp",
                "inflation",
                "referendum",
                "court",
                "verdict",
            ]
        ):
            return AgentResult(
                status="rejected",
                reject_reason="mock agent only generates for clear scheduled or result-oriented events",
            )

        prediction_date = (event.event_date - timedelta(days=1)).isoformat()
        source = event.evidence_urls[0] if event.evidence_urls else event.source_url or ""
        payload = AgentCandidatePayload(
            event_name=event.title,
            domain=_mock_domain(event),
            question=f"On {event.event_date.isoformat()}, what will be the outcome of {event.title}?",
            prediction_date=prediction_date,
            ground_truth=event.summary or event.title,
            resolution_source=source,
        )
        return AgentResult(status="candidate", candidate=payload)


class OpenAICompatibleQuestionAgent(QuestionAgent):
    """Agent backed by an OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0.2,
        max_retries: int = 2,
        timeout_seconds: int = 60,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.prompt_builder = prompt_builder or PromptBuilder()

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_retries: int = 2,
    ) -> "OpenAICompatibleQuestionAgent":
        resolved_model = model or os.getenv("QUESTION_AGENT_MODEL")
        if not resolved_model:
            raise ValueError("--model or QUESTION_AGENT_MODEL is required")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for openai_compatible provider")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions")
        resolved_temperature = (
            temperature
            if temperature is not None
            else float(os.getenv("QUESTION_AGENT_TEMPERATURE", "0.2"))
        )
        return cls(
            model=resolved_model,
            api_key=api_key,
            base_url=base_url,
            temperature=resolved_temperature,
            max_retries=max_retries,
        )

    def generate(self, event: CandidateEvent) -> AgentResult:
        parse_error: str | None = None
        raw_output = ""
        for _attempt in range(self.max_retries + 1):
            try:
                raw_output = self._call_model(event, parse_error=parse_error)
            except requests.RequestException as exc:
                return AgentResult(
                    status="parse_error",
                    reject_reason=f"model request failed: {exc}",
                    raw_output=raw_output,
                )
            result = parse_agent_output(raw_output)
            if result.status != "parse_error":
                return result
            parse_error = result.reject_reason
        return AgentResult(
            status="parse_error",
            reject_reason=parse_error or "model output could not be parsed",
            raw_output=raw_output,
        )

    def _call_model(self, event: CandidateEvent, *, parse_error: str | None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": self.prompt_builder.build_messages(event, parse_error=parse_error),
        }
        response = requests.post(
            _chat_completions_url(self.base_url),
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"])


def build_question_agent(
    *, provider: str, model: str | None, temperature: float, max_retries: int
) -> QuestionAgent:
    """Factory used by the CLI."""
    if provider == "mock":
        return MockQuestionAgent()
    if provider == "openai_compatible":
        return OpenAICompatibleQuestionAgent.from_env(
            model=model, temperature=temperature, max_retries=max_retries
        )
    raise ValueError(f"Unknown agent provider: {provider}")


def parse_agent_output(raw_output: str) -> AgentResult:
    """Parse strict JSON candidate/rejection output from the model."""
    try:
        data = json.loads(_extract_json_text(raw_output))
    except json.JSONDecodeError as exc:
        return AgentResult(
            status="parse_error",
            reject_reason=f"invalid JSON: {exc.msg}",
            raw_output=raw_output,
        )
    if not isinstance(data, dict):
        return AgentResult(
            status="parse_error",
            reject_reason="model output must be a JSON object",
            raw_output=raw_output,
        )
    if "reject_reason" in data and not _has_candidate_fields(data):
        try:
            rejected = AgentRejectedPayload.model_validate(data)
        except ValidationError as exc:
            return AgentResult(
                status="parse_error",
                reject_reason=f"invalid rejected payload: {exc}",
                raw_output=raw_output,
            )
        return AgentResult(
            status="rejected",
            reject_reason=rejected.reject_reason,
            raw_output=raw_output,
        )
    try:
        candidate = AgentCandidatePayload.model_validate(data)
        _validate_candidate_date(candidate.prediction_date)
    except (ValidationError, ValueError) as exc:
        return AgentResult(
            status="parse_error",
            reject_reason=f"invalid candidate payload: {exc}",
            raw_output=raw_output,
        )
    return AgentResult(status="candidate", candidate=candidate, raw_output=raw_output)


def _event_payload(event: CandidateEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "source": event.source,
        "domain": event.domain,
        "event_date": event.event_date.isoformat(),
        "title": event.title,
        "summary": event.summary,
        "source_url": event.source_url,
        "evidence_urls": event.evidence_urls,
    }


def _extract_json_text(raw_output: str) -> str:
    cleaned = raw_output.strip()
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if code_block:
        return code_block.group(1).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        return cleaned[first : last + 1].strip()
    return cleaned


def _has_candidate_fields(data: dict[str, Any]) -> bool:
    required = {
        "event_name",
        "domain",
        "question",
        "prediction_date",
        "ground_truth",
    }
    return any(field in data for field in required)


def _validate_candidate_date(value: str) -> None:
    from datetime import date

    date.fromisoformat(value)


def _chat_completions_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    if cleaned.endswith("/v1"):
        return f"{cleaned}/chat/completions"
    return cleaned


def _mock_domain(event: CandidateEvent) -> str:
    if event.domain == "macro":
        return "macro"
    if event.domain == "public_risk":
        return "public_risk"
    return "politics"
