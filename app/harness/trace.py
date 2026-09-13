from app.harness.models import TraceEvent, TraceEventType


class TraceCollector:
    """Only runner-authored summaries; never pass adapter payloads here."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    @classmethod
    def from_snapshot(cls, events: list[TraceEvent]) -> "TraceCollector":
        collector = cls()
        collector._events = [event.model_copy(deep=True) for event in events]
        return collector

    def record(
        self, iteration: int, event_type: TraceEventType, name: str,
        success: bool, summary: str,
    ) -> None:
        self._events.append(TraceEvent(
            sequence=len(self._events) + 1, iteration=iteration,
            event_type=event_type, name=name[:64], success=success, summary=summary[:200],
        ))

    def snapshot(self) -> list[TraceEvent]:
        return [event.model_copy(deep=True) for event in self._events]
