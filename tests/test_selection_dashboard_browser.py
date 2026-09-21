# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def test_selection_dashboard_does_not_request_forbidden_analytics() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <main id="selection-dashboard" data-can-read-analytics="false">
              <div id="stat-candidates"></div><div id="stat-library"></div>
              <div id="stat-adopted"></div><div id="stat-drama-count"></div>
              <div id="stat-comment-count"></div><div id="stat-transcript-count"></div>
              <div id="stat-task-count"></div><div id="dashboard-error" class="d-none"></div>
            </main>
            """
        )
        page.evaluate(
            """() => {
              window.requestedUrls = [];
              window.aiworkerApi = {
                request: async (url) => {
                  window.requestedUrls.push(url);
                  return {ok: true, json: async () => ({items: []})};
                },
                parseErrorDetail: async () => 'forbidden',
                isAbortError: () => false,
              };
              window.aiworkerUi = {
                requestKey: async (_key, operation) => operation(undefined),
                announce: () => {},
              };
            }"""
        )
        page.add_script_tag(path=str(ROOT / "static/js/selection/dashboard.js"))
        page.wait_for_function("document.querySelector('#stat-library').textContent !== ''")

        assert page.evaluate("window.requestedUrls") == [
            "/api/creative/topics?limit=50&include_lifecycle=false"
        ]
        for metric in (
            "stat-drama-count", "stat-comment-count", "stat-transcript-count", "stat-task-count"
        ):
            assert page.locator(f"#{metric}").inner_text() == "—"
        assert page.locator("#dashboard-error").inner_text() == ""
        browser.close()


def test_selection_dashboard_starts_analytics_without_waiting_for_topics() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <main id="selection-dashboard" data-can-read-analytics="true">
              <div id="stat-candidates"></div><div id="stat-library"></div>
              <div id="stat-adopted"></div><div id="stat-drama-count"></div>
              <div id="stat-comment-count"></div><div id="stat-transcript-count"></div>
              <div id="stat-task-count"></div><div id="dashboard-error" class="d-none"></div>
            </main>
            """
        )
        page.evaluate(
            """() => {
              window.requestedUrls = [];
              window.aiworkerApi = {
                request: (url) => {
                  window.requestedUrls.push(url);
                  if (url.includes('/topics?')) {
                    return new Promise(resolve => {
                      window.resolveTopics = () => resolve({
                        ok: true,
                        json: async () => ({items: []}),
                      });
                    });
                  }
                  return Promise.resolve({
                    ok: true,
                    json: async () => ({
                      drama_count: 1,
                      comment_count: 2,
                      transcript_count: 3,
                      task_count: 4,
                    }),
                  });
                },
                parseErrorDetail: async () => 'failed',
                isAbortError: () => false,
              };
              window.aiworkerUi = {
                requestKey: async (_key, operation) => operation(undefined),
                announce: () => {},
              };
            }"""
        )

        page.add_script_tag(path=str(ROOT / "static/js/selection/dashboard.js"))

        page.wait_for_function(
            "window.requestedUrls.includes('/api/analytics/overview')",
            timeout=2_000,
        )
        assert page.locator("#stat-drama-count").inner_text() == "1"
        page.evaluate("window.resolveTopics()")
        page.wait_for_function("document.querySelector('#stat-library').textContent === '0'")
        browser.close()
