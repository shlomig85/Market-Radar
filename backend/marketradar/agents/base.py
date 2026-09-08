"""Agent contract.

An agent here is a **typed function with a budget and a trace**, not a chat session:

* a Pydantic input schema and a Pydantic output schema, both validated;
* an explicit budget (LLM calls, searches, wall clock) and stopping rules;
* an ``agent_runs`` row written on every execution — success, failure, timeout or
  budget stop — carrying the model, prompt version, consumption and both envelopes.

That last property is what makes the research trace in the UI a *record* rather than a
decoration: if no agent ran, there is no row, and nothing is displayed.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, RunStatus
from marketradar.domain.models import AgentRun
from marketradar.errors import BudgetExceededError, MarketRadarError
from marketradar.logging import bind_context, get_logger

log = get_logger(__name__)

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass
class AgentBudget:
    """What an agent is allowed to spend. Exceeding any limit stops it."""

    max_llm_calls: int = 2
    max_searches: int = 30
    max_seconds: float = 120.0
    max_output_items: int = 40

    def to_dict(self) -> dict:
        return {
            "max_llm_calls": self.max_llm_calls,
            "max_searches": self.max_searches,
            "max_seconds": self.max_seconds,
            "max_output_items": self.max_output_items,
        }


@dataclass
class AgentConsumption:
    """What an agent actually spent."""

    llm_calls: int = 0
    searches: int = 0
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "searches": self.searches,
            "seconds": round(self.seconds, 3),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
        }


@dataclass
class AgentContext:
    """Everything an agent needs from its caller."""

    session: Session
    research_run_id: str | None = None
    budget: AgentBudget = field(default_factory=AgentBudget)
    consumption: AgentConsumption = field(default_factory=AgentConsumption)
    as_of: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def spend_llm_call(self) -> None:
        if self.consumption.llm_calls >= self.budget.max_llm_calls:
            raise BudgetExceededError(
                "LLM call budget exhausted", limit=self.budget.max_llm_calls
            )
        self.consumption.llm_calls += 1

    def spend_search(self) -> None:
        if self.consumption.searches >= self.budget.max_searches:
            raise BudgetExceededError(
                "Search budget exhausted", limit=self.budget.max_searches
            )
        self.consumption.searches += 1


@dataclass
class AgentResult(Generic[OutputT]):
    """The outcome of one agent execution."""

    status: RunStatus
    output: OutputT | None
    agent_run_id: str
    strategy: str
    error: str | None = None
    consumption: AgentConsumption = field(default_factory=AgentConsumption)

    @property
    def succeeded(self) -> bool:
        return self.status == RunStatus.SUCCEEDED and self.output is not None


class Agent(ABC, Generic[InputT, OutputT]):
    """Base class. Subclasses implement ``execute`` and declare their schemas."""

    name: str
    version: str
    input_model: type[InputT]
    output_model: type[OutputT]

    @abstractmethod
    def execute(self, ctx: AgentContext, payload: InputT) -> OutputT:
        """Do the work. Raise to fail; the wrapper records the failure."""

    def strategy(self, ctx: AgentContext) -> str:
        """Which implementation path this run will take. Recorded and displayed."""
        return "rule_based"

    def model_name(self, ctx: AgentContext) -> str | None:
        return None

    def prompt_version(self, ctx: AgentContext) -> str | None:
        return None

    # ------------------------------------------------------------------
    def run(self, ctx: AgentContext, payload: InputT, data_mode: DataMode) -> AgentResult[OutputT]:
        """Execute with tracing, budget enforcement and failure capture."""
        strategy = self.strategy(ctx)
        started = datetime.now(tz=UTC)
        clock = time.monotonic()

        record = AgentRun(
            agent_name=self.name,
            agent_version=self.version,
            research_run_id=ctx.research_run_id,
            status=RunStatus.RUNNING,
            started_at=started,
            strategy=strategy,
            model=self.model_name(ctx),
            prompt_version=self.prompt_version(ctx),
            input_payload=payload.model_dump(mode="json"),
            budget=ctx.budget.to_dict(),
            data_mode=data_mode,
        )
        ctx.session.add(record)
        ctx.session.flush()
        bind_context(agent_run_id=record.id, agent=self.name)

        status = RunStatus.FAILED
        output: OutputT | None = None
        error: str | None = None
        try:
            output = self.execute(ctx, payload)
            elapsed = time.monotonic() - clock
            if elapsed > ctx.budget.max_seconds:
                status = RunStatus.TIMED_OUT
                error = (
                    f"Agent exceeded its time budget: {elapsed:.1f}s > "
                    f"{ctx.budget.max_seconds:.1f}s"
                )
                output = None
            else:
                status = RunStatus.SUCCEEDED
        except BudgetExceededError as exc:
            status = RunStatus.BUDGET_EXCEEDED
            error = exc.message
        except (PydanticValidationError, MarketRadarError) as exc:
            status = RunStatus.FAILED
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - failure must be recorded, not swallowed
            status = RunStatus.FAILED
            error = f"{type(exc).__name__}: {exc}"

        ctx.consumption.seconds = time.monotonic() - clock
        finished = datetime.now(tz=UTC)
        record.status = status
        record.finished_at = finished
        record.duration_ms = int((finished - started).total_seconds() * 1000)
        record.output_payload = output.model_dump(mode="json") if output else None
        record.consumed = ctx.consumption.to_dict()
        record.error = error
        ctx.session.flush()

        if error:
            log.warning("agent.failed", agent=self.name, status=status.value, error=error)
        else:
            log.info("agent.succeeded", agent=self.name, duration_ms=record.duration_ms)

        return AgentResult(
            status=status,
            output=output,
            agent_run_id=record.id,
            strategy=strategy,
            error=error,
            consumption=ctx.consumption,
        )
