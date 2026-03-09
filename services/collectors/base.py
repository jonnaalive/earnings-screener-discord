"""수집기 기본 클래스."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from models.schemas import EarningsEvent

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """모든 수집기의 기본 클래스."""

    name: str = "base"

    @abstractmethod
    async def collect(self, lookback_days: int = 3) -> list[EarningsEvent]:
        """최근 lookback_days 내의 실적 발표 이벤트를 수집."""
        ...

    def _log_result(self, events: list[EarningsEvent]):
        logger.info("[%s] Collected %d earnings events", self.name, len(events))
