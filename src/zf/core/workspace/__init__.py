"""Workspace metadata helpers.

Workspace state is local metadata only. Project truth remains under each
project's configured runtime state dir.
"""

from zf.core.workspace.project_initializer import (
    ProjectInitResult,
    ProjectInitializer,
)
from zf.core.workspace.project_admission import (
    ProjectAdmissionAction,
    inspect_project_admission,
    normalize_new_project_metadata,
)
from zf.core.workspace.project_instruction_docs import ProjectInstructionDocsResult
from zf.core.workspace.lifecycle import ProjectLifecycle, project_lifecycle
from zf.core.workspace.project_resolver import (
    ProjectResolution,
    ProjectResolver,
)
from zf.core.workspace.providers import (
    WorkspaceProviderRegistry,
    providers_path,
)
from zf.core.workspace.registry import (
    WorkspaceProject,
    WorkspaceRegistry,
    legacy_project_id,
    stable_project_id,
)
from zf.core.workspace.runtime_manager import RuntimeManager

__all__ = [
    "ProjectInitResult",
    "ProjectAdmissionAction",
    "ProjectInstructionDocsResult",
    "ProjectInitializer",
    "ProjectLifecycle",
    "ProjectResolution",
    "ProjectResolver",
    "RuntimeManager",
    "WorkspaceProviderRegistry",
    "WorkspaceProject",
    "WorkspaceRegistry",
    "legacy_project_id",
    "inspect_project_admission",
    "normalize_new_project_metadata",
    "project_lifecycle",
    "providers_path",
    "stable_project_id",
]
