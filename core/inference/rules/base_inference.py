"""
core/inference/rules/base_inference.py
========================================
Abstract base class for all inference rules.

An InferenceRule runs AFTER the CorrelationEngine has finished.  It reads the
(now partially correlated) timeline and either:

  a) Attaches behavioral FLAGS to existing events — e.g. "LATE_NIGHT_ACTIVITY"
  b) Synthesises NEW INFERRED events that represent conclusions drawn from patterns
     (e.g. "FACTORY_RESET_INDICATOR" triggered by an absence of call log entries)

Design contract:
  - Flag-attachment rules: only add flags to existing events, no new events.
  - Event-synthesis rules: return new TimelineEvent objects with
    evidence_type="INFERRED" and inferred_from set to source event IDs.
  - All rules are deterministic: same input → same output (no randomness,
    no probabilistic scoring).
  - Rules must never remove existing events.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models.timeline_event import TimelineEvent


class InferenceRule(ABC):
    """
    Abstract base for all inference / behavioral pattern rules.

    Subclasses implement ``apply(timeline)`` which:
      - Mutates existing TimelineEvent flags in-place.
      - Returns a (possibly empty) list of newly synthesised INFERRED events.

    The caller (InferenceEngine) merges returned events into the timeline
    and re-sorts chronologically.
    """

    #: Human-readable rule identifier shown in analysis reports.
    NAME: str = "UnnamedInference"

    #: Short description of what this rule detects.
    DESCRIPTION: str = ""

    @abstractmethod
    def apply(self, timeline: list[TimelineEvent]) -> list[TimelineEvent]:
        """
        Evaluate the timeline and apply this inference rule.

        Args:
            timeline: Full list of TimelineEvents (may be mutated in-place
                      to add flags to existing events).

        Returns:
            List of newly synthesised INFERRED TimelineEvents (empty if none).
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self.NAME}')"
