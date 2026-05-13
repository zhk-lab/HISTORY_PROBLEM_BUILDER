# Plan: Reduce Pre-Screen and Agent False Rejections

## Summary

The current pipeline rejects many events in `pre_screen` and `agent`. Some rejections are correct because Wikipedia Current Events contains many retrospective spot-news items, but the current `screening.py` design is too keyword-centered. The next change should turn pre-screening from a keyword filter into a lightweight, explainable event-structure classifier.

The goal is to improve recall for events that genuinely support historical prediction questions, while keeping the core quality rule: do not turn a news sentence into a direct "Will X happen?" rewrite.

## Current Problems

- Keyword matching is too shallow. A word such as `court`, `summit`, or `announce` does not prove that an event has a real forecast setup.
- Keyword matching misses valid events. Sports finals, awards, official votes, policy effective dates, and scheduled public releases may not contain current high-value words.
- `other` and `conflict` domains are rejected too aggressively even when the event has a scheduled process or clear result.
- Network/model request failures are mixed into rejected events, which makes rejection analysis misleading.
- Some duplicated Wikipedia records reach the agent separately and can produce inconsistent generate/reject outcomes.

## Screening Redesign

Keep the existing keyword lists only as weak signals. They should not be the final decision mechanism.

Introduce a structured intermediate object, for example `ScreenSignals`, with these dimensions:

- `source_quality`: whether the source is naturally high-value, such as election calendars, FOMC, BLS, or FRED.
- `public_source`: whether the event has `source_url` or `evidence_urls`.
- `event_type`: a coarse event class such as election, court, sports final, official release, disaster recovery, diplomatic negotiation, or immediate casualty news.
- `forecast_setup`: whether the event has a natural pre-outcome prediction point, such as scheduled vote, final, official release, deadline, ruling, or policy effective date.
- `resolution_signal`: whether the text gives a resolvable outcome, such as won, ruled, passed, vote count, seat total, score, reported percentage, or formal status.
- `rewrite_risk`: whether the only likely question is a direct news rewrite.

Decision rule:

- Select high-quality structured sources when public evidence exists.
- Select events with both `forecast_setup` and `resolution_signal`.
- Select clear borderline events for agent review when they are not obvious spot news.
- Reject events with high `rewrite_risk` and no forecast setup.
- Reject events with no public source.
- Keep low-priority sports/entertainment behavior behind existing opt-in unless the event has a strong structured result.

## Concrete Behavior Targets

Events that should still be rejected:

- Traffic accident deaths.
- Airstrike casualty reports.
- Ordinary arrests or detentions.
- Ordinary protests.
- Ordinary statements or phone calls without a deadline or formal process.
- Diplomatic comments with no objective resolving status.

Events that should reach the agent:

- Kentucky Derby winner.
- Polish Cup final result.
- Court rulings with a formal legal disposition.
- Parliamentary no-confidence votes.
- Official economic/statistical releases.
- Policies or operations with a clear scheduled start or effective date.

Events that may be borderline:

- Diplomatic negotiation status.
- Ceasefire status by a deadline.
- Investigation milestones.
- Events with possible future deadlines but incomplete resolution text.

## Implementation Changes

- In `screening.py`, add comments to each current keyword collection explaining that the lists are weak signals, not final proof.
- Add `ScreenSignals` and move keyword checks into signal extraction helpers.
- Replace the current direct if/else keyword decision flow with a small decision layer based on signals.
- Add clearer reject reasons: `missing_public_source`, `immediate_news_without_forecast_setup`, `missing_resolution_signal`, `rewrite_risk_too_high`, `low_priority_without_opt_in`, and `domain_requires_structured_signal`.
- Add a `borderline_forecast_signal` or equivalent selected reason for events that should be sent to the agent despite imperfect domain classification.
- Improve Wikipedia domain inference so sports finals, court/legal events, elections/votes, and macro/statistical events are not all left as `other`.
- Keep agent prompt changes conservative: allow elections, court rulings, official votes, sports finals, and scheduled public releases when the event text gives a clear result; continue forbidding direct `Will X happen?` rewrites.
- Separate model request failures from semantic rejections by writing retryable failures to `failed_question_events_{date_range}.jsonl`.

## Public Interface Changes

- Keep existing CLI commands compatible.
- Add optional output: `failed_question_events_{date_range}.jsonl`.
- Keep `rejected_question_candidates_{date_range}.jsonl` for real semantic or validation rejections only.
- Optional future CLI flag: `--include-borderline`, used to send more borderline `other/conflict` events to the agent.

## Test Plan

- Unit test signal extraction for immediate news, sports final, court ruling, official vote, and scheduled release examples.
- Verify obvious spot news is still rejected.
- Verify Kentucky Derby, Polish Cup final, court ruling, no-confidence vote, and scheduled start examples are not rejected only because their domain is `other` or `conflict`.
- Verify request failures are written to failed output, not rejected output.
- Verify the existing mock pipeline still writes candidates and rejected files.
- Run an end-to-end sample on `data/event/events_2026-05-01_to_2026-05-10.jsonl` and compare counts before/after.

## Acceptance Criteria

- Pre-screen decisions no longer depend mainly on one keyword list.
- Each rejected event has a reason that explains whether the issue is missing forecast setup, missing resolution, rewrite risk, missing source, or low priority.
- Recall improves for sports finals, court rulings, official votes, and scheduled releases.
- Immediate casualty/news reports remain consistently rejected.
- Model/network failures are no longer counted as content rejections.
