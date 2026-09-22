# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Template-driven screenplay export domain."""

from creative.screenplay_export.model import (
    CharacterExport,
    EpisodeExport,
    ExportMetadata,
    SceneExport,
    ScriptBlockExport,
    ScriptExportDocument,
)
from creative.screenplay_export.normalize import build_export_document
from creative.screenplay_export.renderers import (
    export_filename,
    export_media_type,
    render_export_document,
)
from creative.screenplay_export.templates import (
    TemplateSpec,
    get_template_spec,
    list_template_specs,
)

__all__ = [
    "CharacterExport",
    "EpisodeExport",
    "ExportMetadata",
    "SceneExport",
    "ScriptBlockExport",
    "ScriptExportDocument",
    "TemplateSpec",
    "build_export_document",
    "export_filename",
    "export_media_type",
    "get_template_spec",
    "list_template_specs",
    "render_export_document",
]
