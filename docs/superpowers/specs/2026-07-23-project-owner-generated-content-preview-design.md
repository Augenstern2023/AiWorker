# Project Owner Generated Content Preview Design

## Goal

Allow a project owner to open the generated creative material represented by a
topic-library lifecycle badge, inspect the newest result by default, switch to
older versions of the same project and stage, and download the selected
version to the browser's local machine.

## Scope

The feature covers these generated stages:

- Imitation Brief.
- Character relationships.
- Episode outlines.
- Beats scripts.

It is available from a clickable generated lifecycle badge in the topic
library. It does not add editing, generation, submission, approval, or server
delivery creation to the project-owner experience.

## Access Model

The preview API resolves the project associated with the topic, then permits
only its managing project owner or a system administrator. It uses the existing
project-management checks; a client-side hidden button is not an authorization
mechanism. Unauthorized requests return `403`; an absent project, stage, or
generated version returns `404`.

## Data and API

The backend exposes a project-owner read-only endpoint for generated content by
topic and stage. Its response includes the associated project, the newest
version as the selected version, and metadata for every historic version of the
same stage. Selecting another version retrieves that immutable resource or
version snapshot without changing its status.

A separate authenticated download endpoint serializes the selected version as
Markdown or JSON and returns it with `Content-Disposition: attachment`. It
does not call the audited server-delivery workflow or write any exported file
to the server.

## User Experience

Generated lifecycle badges become compact buttons while all other lifecycle
badges remain static. Selecting one opens a read-only modal:

- Header: stage label and project name.
- Version selector: newest generated version selected initially; historic
  versions ordered newest first.
- Body: stage-specific structured content rendered read-only.
- Actions: local Markdown and JSON downloads, plus close.

The modal has loading, empty, permission, and request-error states. It contains
no save, generate, submit, approve, or edit controls.

## Testing

Backend tests cover managed-project access, rejected access, default newest
version selection, historic-version selection, and attachment download headers.
UI tests cover generated badges, the modal controls, and calls to the new API.

