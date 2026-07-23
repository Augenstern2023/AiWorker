# Stage-Aware Workflow Assignment Design

## Goal

Make the assignment action on the topic library select the correct execution role
from the project's current workflow stage rather than always assigning a creative
planner.

## Stage Mapping

| Project lifecycle stage | Workflow task | Assigned role |
| --- | --- | --- |
| `pending_character_relationships` | `characters` | `creative_planner` |
| `pending_episode_outlines` | `outline` | `screenwriter` |
| `pending_beats` | `beats` | `screenwriter` |

The assignment button is only shown when the topic has an approved breakdown and
the lifecycle stage maps to one of these assignable workflow tasks. Its label,
dialog title, candidate query, empty-state message, and result summary use the
mapped role and task labels.

## Data Flow

The browser derives an assignment descriptor from `topic.lifecycle_status` and
requests candidates using its `role_code` and `task_type`. It submits the selected
user to a stage-aware backend endpoint. The endpoint verifies that the topic's
current project stage still matches the requested descriptor before assigning the
matching existing workflow task and adding the selected user as the corresponding
project member.

The existing initial planning-assignment endpoint remains responsible only for
creating a project from an adopted topic and assigning its first `brief` task to a
creative planner. It is not used to reassign later project stages.

## Error Handling

If the project has moved to another stage before confirmation, the server rejects
the request with a conflict response and the UI asks the user to refresh. The UI
also shows a clear error when no active user can perform the mapped task.

## Tests

Add API and UI coverage verifying that characters map to creative planners, while
outlines and beats map to screenwriters. Cover stale-stage rejection so a cached
dialog cannot assign the wrong role after workflow progress changes.
