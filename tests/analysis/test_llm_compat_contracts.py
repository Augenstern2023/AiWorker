# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import httpx
import pytest

from analysis import llm


@pytest.mark.asyncio
@pytest.mark.parametrize("wire_api", ["chat_completions", "responses"])
async def test_truncation_precedes_json_repair_and_keeps_partial_text(wire_api):
    """Catch repaired partial JSON being accepted or truncation losing its base contract."""
    partial = '{"title":"半段'
    payload = (
        {"choices": [{"finish_reason": "length", "message": {"content": partial}}]}
        if wire_api == "chat_completions"
        else {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": partial}]}],
        }
    )
    client = llm.OpenAICompatibleLLMProvider(
        base_url="https://example.test/v1", api_key="key", model="test",
        wire_api=wire_api,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    with pytest.raises(llm.LLMIncompleteResponseError) as caught:
        await client.complete_json([{"role": "user", "content": "json"}])
    assert type(caught.value).__name__ == "LLMOutputTruncatedError"
    assert caught.value.finish_reason == ("length" if wire_api == "chat_completions" else "max_output_tokens")
    assert caught.value.returned_chars == len(partial)
    assert caught.value.partial_text == partial
