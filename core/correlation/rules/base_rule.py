"""
core/correlation/rules/base_rule.py
=====================================
Abstract base class for all correlation rules.

A CorrelationRule scans the full timeline and identifies pairs (or groups)
of DIRECT events that are causally or temporally linked.  When a match is
found it:
  1. Creates a shared correlation_id (UUID4)
  2. Calls promote_to("CORRELATED") on each matched event
  3. Calls link_correlation() to record the peer relationships

Design contract:
  - Rules are ADDITIVE: they only upgrade and link — never delete events.
  - Rules must not assume any ordering other than chronological.
  - Rules communicate through TimelineEvent mutation only; no side-channel state.
  - Each rule is independently instantiable and testable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models.timeline_event import TimelineEvent


class CorrelationRule(ABC):
    """
    Abstract base for all correlation rules.

    Subclasses implement ``apply(timeline)`` which mutates matched TimelineEvents
    in-place (promoting their classification and linking them).

    Returns the number of correlation groups created (for reporting).
    """

    #: Human-readable name shown in correlation reports.
    NAME: str = "UnnamedRule"

    #: Short description for audit logs.
    DESCRIPTION: str = ""

    @abstractmethod
    def apply(self, timeline: list[TimelineEvent]) -> int:
        """
        Scan the timeline and apply this correlation rule.

        Args:
            timeline: The full list of TimelineEvents (mutated in-place).

        Returns:
            Number of new correlation groups created by this rule.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self.NAME}')"
