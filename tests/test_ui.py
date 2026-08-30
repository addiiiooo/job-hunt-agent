"""Phase 7: the two pages.

The trace table is the demo, so it gets the most assertions."""

from __future__ import annotations

import re

from tests.fakes import R_K8S, R_POSTGRES, full_run_script, planner_reply

JD = "Backend Engineer. Python, PostgreSQL, Kubernetes."


def _analyse(client, scripted, replies):
    scripted(replies)
    r = client.post("/analyse", data={"jd_text": JD})
    assert r.status_code == 200
    return r.text


def test_index_is_a_textarea_and_a_button_and_nothing_else(client):
    body = client.get("/").text
    assert body.count("<textarea") == 1
    assert body.count("<button") == 1
    assert "<script" not in body


def test_headline_shows_hard_requirements_met(client, scripted, corpus_index):
    html = _analyse(client, scripted, full_run_script(corpus_index))
    assert re.search(r"<strong>\s*1\s*/\s*2\s*</strong>\s*hard requirements met", html)
    assert "Backend Engineer, Payments Platform" in html


def test_requirement_table_has_a_pill_evidence_and_a_rationale(client, scripted, corpus_index):
    html = _analyse(client, scripted, full_run_script(corpus_index))
    assert 'class="pill pill-met"' in html
    assert 'class="pill pill-partial"' in html
    assert 'class="pill pill-gap"' in html
    assert "EXPLAIN ANALYZE" in html                      # a cited snippet
    assert "query-plan work is evidenced" in html          # a rationale
    assert "resume#databases" in html                      # the citation itself


def test_refusals_are_shown_as_refusals_not_hidden(client, scripted, corpus_index):
    html = _analyse(client, scripted, full_run_script(corpus_index))
    assert "<strong>Refused</strong>" in html
    assert "failed grounding validation" in html
    assert R_K8S in html
    # the fabricated bullet text must not survive into the page
    assert "Operated production Kubernetes clusters at scale" not in html
    # the honest one must
    assert "Removed sequential scans from hot paths" in html


def test_grounded_bullet_shows_what_it_is_grounded_in(client, scripted, corpus_index):
    html = _analyse(client, scripted, full_run_script(corpus_index))
    assert "grounded in" in html
    assert R_POSTGRES in html


def test_trace_table_renders_every_step_with_why_latency_tokens(client, scripted, corpus_index):
    html = _analyse(client, scripted, full_run_script(corpus_index))
    body = html.split('<h2>Trace</h2>')[1]

    for tool in ["parse_requirements", "retrieve_evidence", "score_match", "draft_bullets", "finish"]:
        assert f"<code>{tool}</code>" in body
    assert "the job description is not parsed yet" in body     # why chosen
    assert "the report and the bullets are both complete" in body
    assert " ms" in body and "in /" in body and "out" in body
    assert "7 steps" in body
    assert "terminated: <code>complete</code>" in body


def test_failed_steps_appear_in_the_trace_with_their_taxonomy_kind(client, scripted, corpus_index):
    html = _analyse(client, scripted, [planner_reply("teleport"), planner_reply("finish")])
    assert 'class="step-error"' in html
    assert '<code class="err">tool_execution</code>' in html
    assert "no such tool" in html


def test_early_termination_is_announced_rather_than_hidden(client, scripted, corpus_index):
    from app import config
    from app.main import app

    app.state.settings = config.Settings(
        anthropic_api_key="sk-test", db_path=":memory:", max_steps=2
    )
    html = _analyse(
        client, scripted, [planner_reply("retrieve_evidence", requirement="Python") for _ in range(6)]
    )
    assert "This run ended early" in html
    assert "<code>max_steps</code>" in html


def test_pages_are_plain_html_with_one_stylesheet(client, scripted, corpus_index):
    html = _analyse(client, scripted, full_run_script(corpus_index))
    assert html.count('rel="stylesheet"') == 1
    for framework in ["react", "vue", "svelte", "cdn.tailwind", "bootstrap"]:
        assert framework not in html.lower()
    assert client.get("/static/style.css").status_code == 200
