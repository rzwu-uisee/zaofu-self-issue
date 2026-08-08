"""Compatibility imports for the historical deterministic-runtime types."""

from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


# Compatibility for extensions and replay code that still import the
# historical deterministic-runtime name.
OrchestratorDecision = WorkflowRuntimeDecision


__all__ = ["WorkflowRuntimeDecision", "OrchestratorDecision"]
