# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Immutable, renderer-independent screenplay export model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScriptBlockExport:
    """One ordered screenplay body block."""

    type: str
    text: str = ""
    speaker: str = ""
    modifier: str = ""

    @property
    def block_type(self) -> str:
        """Compatibility alias that reads more clearly in renderer code."""

        return self.type


@dataclass(frozen=True, slots=True)
class SceneExport:
    """One normalized scene and its ordered body blocks."""

    sequence_no: int
    scene_no: int
    location: str = ""
    time_of_day: str = ""
    interior_exterior: str = ""
    characters: tuple[str, ...] = ()
    props: tuple[str, ...] = ()
    blocks: tuple[ScriptBlockExport, ...] = ()


@dataclass(frozen=True, slots=True)
class EpisodeExport:
    """One current downloadable episode version."""

    episode_number: int
    title: str = ""
    version: int = 1
    scenes: tuple[SceneExport, ...] = ()

    @property
    def number(self) -> int:
        """Short alias used by presentation adapters."""

        return self.episode_number


@dataclass(frozen=True, slots=True)
class CharacterExport:
    """A character biography suitable for optional front matter."""

    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class ExportMetadata:
    """Selection and source-version metadata for one export."""

    project_id: int | str
    stage: str
    episode_numbers: tuple[int, ...]
    versions: tuple[int, ...]
    video_parameter_appendix: str = ""


@dataclass(frozen=True, slots=True)
class ScriptExportDocument:
    """Complete screenplay export AST shared by every human renderer."""

    drama_name: str
    episodes: tuple[EpisodeExport, ...]
    metadata: ExportMetadata
    synopsis: str = ""
    character_bios: tuple[CharacterExport, ...] = ()
    series_overview: tuple[tuple[str, str], ...] = ()

    @property
    def title(self) -> str:
        """Compatibility alias for consumers that call the drama name a title."""

        return self.drama_name
