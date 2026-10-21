"""Tests for `pr_findings.py --rounds`, the kirocrew-prepare-pr loop's cross-round memory.

The view is rebuilt from the PR's own writer-authored disposition comments, so
nothing lives on disk and the record outlives the loop. Three properties are
load-bearing:

- one round per judged `head=`, in first-disposed order, regardless of which
  lane each comment targets;
- the two optional plain lines (`self-added:`, `mechanism:`) are read from the
  comment body OUTSIDE the `> ` block, and a span disposed twice in one round
  counts that round once;
- the retrospective trigger is an EXIT CODE (30): a span FIXED in two earlier
  rounds (SEAM REPEAT; SEAM LIMIT after four), or the next round being the
  3rd/6th/9th. A rebuttal or a deferral answered again is not a fix and never
  triggers it, and no step of the view asks a person.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest
from skill_script_helpers import load_skill_script

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "src"
    / "kiro_crew"
    / "builtin_skills"
    / "kirocrew-dev"
    / "kirocrew-prepare-pr"
    / "scripts"
    / "pr_findings.py"
)


def _load() -> ModuleType:
    return load_skill_script("prepare_pr_findings_rounds", SCRIPT)


def _disp(
    cid: int, target: str, head: str, span: str | None, extra: str = "", word: str = "fixed"
) -> dict:
    marker = "<!-- ai-review-disposition target={} head={} -->\n".format(target, head)
    claim = (
        "**{}** — span={}\n".format(word, span) if span else "**{}** — a watch item\n".format(word)
    )
    body = marker + claim + extra + "\n> rationale line naming span=deadbeef0000 as evidence only\n"
    return {"id": cid, "body": body, "user": {"login": "writer"}}


def _fix_counts(module: ModuleType) -> dict:
    """{span: distinct judged heads that fixed it}, from the same counter `--rounds` prints."""
    by_head, order, _ = module._collect_rounds("o/r", 7)
    return {sp: len(rs) for sp, rs in module._fix_rounds(by_head, order).items()}


def _wire(module: ModuleType, monkeypatch, comments: list[dict]) -> None:
    monkeypatch.setattr(module, "fetch_disposition_comments", lambda repo, number: comments)
    monkeypatch.setattr(
        module,
        "writer_disposition_records",
        lambda repo, cs: [module.parse_disposition_record(c) for c in cs],
    )


def test_rounds_are_grouped_by_judged_head_in_order(monkeypatch, capsys):
    mod = _load()
    h0 = "a" * 40
    h1 = "b" * 40
    comments = [
        _disp(1, "gpt", h0, "aaaaaaaaaaa1", "self-added: no\n"),
        _disp(2, "design", h1, None, "mechanism: retry guard on add\n"),
        _disp(3, "gpt", h1, "aaaaaaaaaaa1", "self-added: yes\n"),
        _disp(4, "gpt", h1, "aaaaaaaaaaa2", "self-added: yes\nmechanism: digest suffix on path\n"),
    ]
    _wire(mod, monkeypatch, comments)
    rc = mod.rounds_view("o/r", 7, "c" * 40, {"additions": 10, "deletions": 2})
    out = capsys.readouterr().out
    assert rc == 30, "two rounds disposed -> the next is the 3rd -> retrospective due"
    assert (
        "- round 0 — head aaaaaaaaaaaa — 1 disposition(s) [gpt×1] — spans: aaaaaaaaaaa1 — self-added: 0"
        in out
    )
    assert (
        "- round 1 — head bbbbbbbbbbbb — 3 disposition(s) [design×1, gpt×2] — spans: aaaaaaaaaaa1, aaaaaaaaaaa2 — self-added: 2"
        in out
    )
    assert "    mechanism: retry guard on add" in out
    assert "    mechanism: digest suffix on path" in out
    assert "size now: +10/-2" in out
    assert "next round: 2" in out
    assert "findings in self-added code: 2   mechanisms declared: 2" in out
    assert "recurring spans (≥3 rounds): none" in out
    # a span= inside a `> ` line is evidence, not a claim
    assert "deadbeef0000" not in out.split("spans:")[1].split("\n")[0]


def test_exit_30_on_third_round_and_on_a_span_at_three(monkeypatch, capsys):
    mod = _load()
    heads = ["1" * 40, "2" * 40, "3" * 40, "4" * 40]
    # two rounds disposed -> next is the 3rd -> due
    _wire(
        mod,
        monkeypatch,
        [_disp(1, "gpt", heads[0], "aaaaaaaaaaa1"), _disp(2, "gpt", heads[1], "aaaaaaaaaaa2")],
    )
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    assert "RETROSPECTIVE DUE" in capsys.readouterr().out
    # four rounds, no span repeated -> next is the 5th -> not due
    _wire(
        mod,
        monkeypatch,
        [
            _disp(1, "gpt", heads[0], "aaaaaaaaaaa1"),
            _disp(2, "gpt", heads[1], "aaaaaaaaaaa2"),
            _disp(3, "gpt", heads[2], "aaaaaaaaaaa3"),
            _disp(4, "gpt", heads[3], "aaaaaaaaaaa4"),
        ],
    )
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0
    # the same span in three rounds -> due regardless; twice in one round counts once
    _wire(
        mod,
        monkeypatch,
        [
            _disp(1, "gpt", heads[0], "aaaaaaaaaaa1"),
            _disp(2, "gpt", heads[1], "aaaaaaaaaaa1"),
            _disp(3, "gpt", heads[1], "aaaaaaaaaaa1"),
            _disp(4, "gpt", heads[2], "aaaaaaaaaaa1"),
            _disp(5, "gpt", heads[3], "aaaaaaaaaaa9"),
        ],
    )
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    assert "  aaaaaaaaaaa1 ×3" in capsys.readouterr().out


def test_size_line_splits_code_and_test_and_never_moves_the_exit(monkeypatch, capsys):
    mod = _load()
    first, now = "a" * 40, "c" * 40
    diffs = {
        first: "src/x.py\t10\t2\ntest/test_x.py\t5\t0\n",
        now: "src/x.py\t40\t5\ntest/test_x.py\t20\t1\nwebsite/electron/mochi/test/machineStore.test.js\t3\t0\n",
    }

    def fake_run(args):
        sha = args[2].rsplit("...", 1)[1]
        return (0, diffs[sha], "") if sha in diffs else (1, "", "No commit found")

    monkeypatch.setattr(mod, "run", fake_run)
    _wire(mod, monkeypatch, [_disp(1, "gpt", first, "aaaaaaaaaaa1")])
    assert mod.rounds_view("o/r", 7, now, {"baseRefName": "main"}) == 0
    out = capsys.readouterr().out
    assert "size at first judged head: code 12, test 5 lines   now: code 45, test 24 lines" in out
    # a first head GitHub cannot read (force-pushed away) reads as unknown
    _wire(mod, monkeypatch, [_disp(1, "gpt", "d" * 40, "aaaaaaaaaaa1")])
    assert mod.rounds_view("o/r", 7, now, {"baseRefName": "main"}) == 0
    assert "size at first judged head: unknown   now: code 45" in capsys.readouterr().out
    # a compare GitHub truncated at its file cap is not a size
    diffs[now] = "src/x.py\t1\t0\n" * mod.COMPARE_FILE_CAP
    assert mod.rounds_view("o/r", 7, now, {"baseRefName": "main"}) == 0
    assert "now: unknown" in capsys.readouterr().out


def test_rounds_view_fails_closed_when_comments_are_unreadable(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "fetch_disposition_comments", lambda repo, number: None)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 2
    monkeypatch.setattr(mod, "fetch_disposition_comments", lambda repo, number: [])
    monkeypatch.setattr(mod, "writer_disposition_records", lambda repo, cs: None)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 2


def test_skill_wires_the_rounds_view_and_the_two_lines():
    skill = (SCRIPT.parent.parent / "SKILL.md").read_text(encoding="utf-8")
    assert "pr_findings.py <pr#> --rounds" in skill
    # The intent lives in the PR body's frozen sections, not in a separate comment.
    assert "prepare-pr-intent" not in skill
    flat = " ".join(skill.split())
    assert "Copy the frozen goal verbatim every round." in flat
    assert "revert to `<head sha>` and redo via a smaller path" in flat
    assert "`self-added: yes|no`" in skill and "`mechanism: <one line>`" in skill
    assert "round_notes" not in skill
    assert "## Three questions per finding" in skill


def test_the_frozen_goal_line_is_printed_from_the_pr_body(monkeypatch, capsys):
    mod = _load()
    _wire(mod, monkeypatch, [])
    body = "## Problem / Motivation\r\n\r\n**Goal:**  Users can resume a chat.  \r\n"
    assert mod.rounds_view("o/r", 7, "f" * 40, {"body": body}) == 0
    assert "goal (frozen): Users can resume a chat.\n" in capsys.readouterr().out


def test_an_unfilled_or_absent_goal_line_reads_as_missing(monkeypatch, capsys):
    mod = _load()
    _wire(mod, monkeypatch, [])
    # The template ships an empty `**Goal:**` scaffold; that is not a goal.
    for pr_json in ({"body": "## Problem / Motivation\n\n**Goal:**\n\nText.\n"}, {}):
        mod.rounds_view("o/r", 7, "f" * 40, pr_json)
        assert "goal (frozen): MISSING" in capsys.readouterr().out


def test_the_goal_line_is_redacted_before_printing(monkeypatch, capsys):
    """The body is PR-controlled, so a credential in it must not reach the transcript."""
    mod = _load()
    _wire(mod, monkeypatch, [])
    secret = "ghp_" + "a" * 36
    mod.rounds_view("o/r", 7, "f" * 40, {"body": "**Goal:** leak token={}\n".format(secret)})
    out = capsys.readouterr().out
    assert "goal (frozen): leak" in out
    assert secret not in out


HEADS = ["1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40, "6" * 40, "7" * 40]
CLASS_A = "class: a hook can come from a source other than core.hooksPath\n"
CLASS_A2 = "class: a git hook can come from a source other than core hooksPath\n"
CLASS_B = "class: the export path drops the trailing newline\n"


def test_a_span_fixed_in_two_heads_prints_seam_repeat_and_exits_30(monkeypatch, capsys):
    mod = _load()
    # four rounds disposed, so the cadence alone (the 5th round) would not fire
    comments = [
        _disp(1, "gpt", HEADS[0], "aaaaaaaaaaa1", CLASS_A),
        _disp(2, "gpt", HEADS[1], "aaaaaaaaaaa1", CLASS_A2),
        _disp(3, "gpt", HEADS[2], "cccccccccc21"),
        _disp(4, "gpt", HEADS[3], "cccccccccc22"),
    ]
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    out = capsys.readouterr().out
    assert "SEAM REPEAT span=aaaaaaaaaaa1 fixed in rounds 0, 1." in out
    assert "SEAM LIMIT" not in out
    assert "remove the second writer" in out and "defer with an issue" in out
    assert "No one needs to approve" in out


def test_a_span_fixed_in_one_head_is_not_a_seam(monkeypatch, capsys):
    mod = _load()
    comments = [_disp(i + 1, "gpt", HEADS[i], "{:011d}{}".format(i, "c")) for i in range(4)]
    comments[1] = _disp(2, "gpt", HEADS[1], "aaaaaaaaaaa1", CLASS_A)
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0
    assert "SEAM" not in capsys.readouterr().out


def test_the_count_is_of_heads_and_the_class_line_is_only_a_printed_label(monkeypatch, capsys):
    mod = _load()
    comments = [
        _disp(1, "gpt", HEADS[0], "aaaaaaaaaaa1", CLASS_A),
        _disp(2, "gpt", HEADS[1], "aaaaaaaaaaa1", CLASS_B),
        _disp(3, "gpt", HEADS[2], "cccccccccc21"),
        _disp(4, "gpt", HEADS[3], "cccccccccc22"),
    ]
    _wire(mod, monkeypatch, comments)
    # two fixes of one span in two heads: a seam, whatever the class lines say
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    out = capsys.readouterr().out
    assert "SEAM REPEAT span=aaaaaaaaaaa1 fixed in rounds 0, 1." in out
    assert "class (label only): the export path drops the trailing newline" in out


def test_two_fixed_records_of_one_head_count_that_head_once_and_keep_every_span(
    monkeypatch, capsys
):
    mod = _load()
    comments = [
        _disp(1, "gpt", HEADS[0], "aaaaaaaaaaa1"),
        _disp(2, "opus", HEADS[0], "aaaaaaaaaaa1"),
        _disp(3, "gpt", HEADS[0], "bbbbbbbbbbb1"),
        _disp(4, "gpt", HEADS[1], "aaaaaaaaaaa1"),
        _disp(5, "gpt", HEADS[1], "bbbbbbbbbbb1", word="rebutted"),
    ]
    _wire(mod, monkeypatch, comments)
    assert _fix_counts(mod) == {"aaaaaaaaaaa1": 2, "bbbbbbbbbbb1": 1}


def test_a_span_with_no_class_sentence_still_counts(monkeypatch, capsys):
    mod = _load()
    comments = [
        _disp(1, "gpt", HEADS[0], "aaaaaaaaaaa1"),
        _disp(2, "gpt", HEADS[1], "aaaaaaaaaaa1", CLASS_A),
        _disp(3, "gpt", HEADS[2], "cccccccccc21"),
        _disp(4, "gpt", HEADS[3], "cccccccccc22"),
    ]
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    assert "SEAM REPEAT span=aaaaaaaaaaa1" in capsys.readouterr().out


def test_a_span_fixed_in_four_heads_prints_seam_limit_with_only_narrow_or_defer(
    monkeypatch, capsys
):
    mod = _load()
    comments = [_disp(i + 1, "gpt", HEADS[i], "aaaaaaaaaaa1", CLASS_A) for i in range(4)]
    comments.append(_disp(9, "gpt", HEADS[4], "cccccccccc21"))
    comments.append(_disp(10, "gpt", HEADS[5], "cccccccccc22"))
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    out = capsys.readouterr().out
    assert "SEAM LIMIT span=aaaaaaaaaaa1 fixed in rounds 0, 1, 2, 3." in out
    assert "SEAM REPEAT" not in out
    assert "narrow the scope, or defer with an issue" in out
    assert "remove the second writer" not in out


def test_answering_a_rebuttal_again_is_not_a_fix(monkeypatch, capsys):
    mod = _load()
    comments = [_disp(i + 1, "gpt", HEADS[i], "aaaaaaaaaaa1", word="rebutted") for i in range(4)]
    _wire(mod, monkeypatch, comments)
    # four rounds, the same span in all four, no fix: mentions alone do not count
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0
    assert "SEAM" not in capsys.readouterr().out


def test_the_class_line_inside_a_quoted_block_is_not_read(monkeypatch, capsys):
    mod = _load()
    quoted = "> class: the export path drops the trailing newline\n"
    comments = [
        _disp(1, "gpt", HEADS[0], "aaaaaaaaaaa1", CLASS_A),
        _disp(2, "gpt", HEADS[1], "aaaaaaaaaaa1", quoted),
        _disp(3, "gpt", HEADS[2], "cccccccccc21"),
        _disp(4, "gpt", HEADS[3], "cccccccccc22"),
    ]
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    out = capsys.readouterr().out
    # the quoted sentence is evidence: the label is the plain line of the first fix
    assert "class (label only): a hook can come from a source other than core.hooksPath" in out
    assert "drops the trailing newline" not in out


def test_the_fix_count_is_of_distinct_heads_whatever_the_class_lines_say(monkeypatch):
    mod = _load()
    comments = [
        _disp(1, "gpt", HEADS[0], "aaaaaaaaaaa1", CLASS_A),
        _disp(2, "gpt", HEADS[1], "aaaaaaaaaaa1", CLASS_A2),
        _disp(3, "gpt", HEADS[2], "aaaaaaaaaaa1", CLASS_A),
        _disp(4, "gpt", HEADS[0], "bbbbbbbbbbb1", CLASS_A),
        _disp(5, "gpt", HEADS[1], "bbbbbbbbbbb1", CLASS_B),
    ]
    _wire(mod, monkeypatch, comments)
    assert _fix_counts(mod) == {"aaaaaaaaaaa1": 3, "bbbbbbbbbbb1": 2}


def test_the_findings_view_prints_a_class_sweep_line_per_span(capsys):
    mod = _load()
    mod.sweep_prompt([])
    assert capsys.readouterr().out == ""
    mod.sweep_prompt(["aaaaaaaaaaa1", "bbbbbbbbbbb1"])
    out = capsys.readouterr().out
    assert "CLASS SWEEP span=aaaaaaaaaaa1:" in out and "CLASS SWEEP span=bbbbbbbbbbb1:" in out
    assert "prepare-pr-sweep.md" in out and "references/seam-protocol.md" in out


def test_the_seam_protocol_reference_carries_the_pick_order_and_the_sweep_format():
    ref = (SCRIPT.parent.parent / "references" / "seam-protocol.md").read_text(encoding="utf-8")
    for needle in (
        "SEAM REPEAT",
        "SEAM LIMIT",
        "prepare-pr-sweep.md",
        "search: none --",
        "resolution: deferred #<issue> pinned-by <test id>",
        "Nothing here asks a person",
    ):
        assert needle in ref
    skill = (SCRIPT.parent.parent / "SKILL.md").read_text(encoding="utf-8")
    assert "references/seam-protocol.md" in skill and "`SEAM LIMIT`" in skill


def test_unlike_class_lines_never_hide_a_seam(monkeypatch, capsys):
    mod = _load()
    comments = [
        _disp(1, "gpt", HEADS[0], "aaaaaaaaaaa1", CLASS_A),
        _disp(2, "gpt", HEADS[1], "aaaaaaaaaaa1", CLASS_B),
        _disp(3, "gpt", HEADS[2], "aaaaaaaaaaa1", CLASS_B),
        _disp(4, "gpt", HEADS[3], "aaaaaaaaaaa1", CLASS_B),
    ]
    _wire(mod, monkeypatch, comments)
    # A, B, B, B: four heads fixed the span, so the next fix is its 5th
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    assert "SEAM LIMIT span=aaaaaaaaaaa1 fixed in rounds 0, 1, 2, 3." in capsys.readouterr().out
    assert _fix_counts(mod) == {"aaaaaaaaaaa1": 4}


def test_the_counter_has_no_class_similarity():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "difflib" not in src and "alike" not in src.lower() and "CLASS_ALIKE" not in src


def test_a_rebuttal_that_quotes_a_fixed_bullet_is_not_a_fix(monkeypatch, capsys):
    mod = _load()
    quote = "> - **fixed** in head 1: the earlier patch\n> Outcome: fixed in abc123\n"
    comments = []
    for i in range(4):
        body = (
            "<!-- ai-review-disposition target=gpt head={} -->\n"
            "**rebutted** — span=aaaaaaaaaaa1\n{}"
        ).format(HEADS[i], quote)
        comments.append({"id": i + 1, "body": body, "user": {"login": "writer"}})
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0, "four rebuttals are not four fixes"
    assert "SEAM" not in capsys.readouterr().out
    assert _fix_counts(mod) == {}


def test_a_fenced_block_never_declares_an_outcome_or_a_class():
    mod = _load()
    body = "<!-- m -->\n**rebutted** — x\n```\n**fixed** — y\nclass: inside a fence\n```\n"
    assert mod.declared_outcome(body) == "rebutted"
    assert mod.declared_class(body) == ""
    assert mod.declared_outcome("```\n**fixed**\n```\n") == ""


@pytest.mark.parametrize(
    ("body", "word"),
    [
        ("<!-- m -->\n**fixed** — span=aaaaaaaaaaa1\n", "fixed"),
        ("<!-- m -->\n- **fixed** — span=aaaaaaaaaaa1\n", "fixed"),
        ("<!-- m -->\n- **span=aaaaaaaaaaa1** fixed in head 1083c00c5\n", "fixed"),
        ("<!-- m -->\ntitle\n\n> Outcome: fixed in 7ff6176c3d. Detail\n", "fixed"),
        ("<!-- m -->\ntitle\n\n> Outcome: checked, no change needed.\n", "checked"),
        ("<!-- m -->\n- **span=aaaaaaaaaaa1** rebutted\n> - **fixed** quoted\n", "rebutted"),
        ("<!-- m -->\n> a quoted line that says fixed\n", ""),
        ("<!-- m -->\nfixed without any marker form\n", ""),
        # a bold title is not an outcome word: scan on to the Outcome line, else nothing
        ("<!-- m -->\n**Refused activation puts a message in the origin chat**\n", ""),
        ("<!-- m -->\n**Fixed point iteration is slow**\n> Outcome: fixed in abc1234\n", "fixed"),
        ("<!-- m -->\n**Retry guard**\n> Outcome: checked, no change needed.\n", "checked"),
        ("<!-- m -->\n**Retry guard**\n> Outcome: rebutted, by design.\n", "rebutted"),
        ("<!-- m -->\n**deferred**\n> Outcome: fixed in abc1234\n", "deferred"),
        ("<!-- m -->\n- **span=aaaaaaaaaaa1** Retry guard fails\n", ""),
    ],
)
def test_the_declared_outcome_is_read_from_the_forms_dispositions_use(body, word):
    assert _load().declared_outcome(body) == word


def test_a_quoted_outcome_line_is_a_fix_in_the_rounds_view(monkeypatch, capsys):
    mod = _load()
    comments = []
    for i in range(2):
        body = (
            "<!-- ai-review-disposition target=gpt head={} -->\n"
            "title of the finding span=aaaaaaaaaaa1\n\n> Outcome: fixed in 7ff6176c3d. Detail.\n"
        ).format(HEADS[i])
        comments.append({"id": i + 1, "body": body, "user": {"login": "writer"}})
    comments.append(_disp(8, "gpt", HEADS[2], "cccccccccc21"))
    comments.append(_disp(9, "gpt", HEADS[3], "cccccccccc22"))
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    assert "SEAM REPEAT span=aaaaaaaaaaa1 fixed in rounds 0, 1." in capsys.readouterr().out


def test_a_bold_title_with_no_outcome_line_counts_nothing(monkeypatch, capsys):
    mod = _load()
    comments = []
    for i in range(4):
        body = (
            "<!-- ai-review-disposition target=gpt head={} -->\n"
            "**Fixed point iteration is slow** span=aaaaaaaaaaa1\n\n> some rationale\n"
        ).format(HEADS[i])
        comments.append({"id": i + 1, "body": body, "user": {"login": "writer"}})
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0
    assert _fix_counts(mod) == {}


def test_evidence_inside_a_blockquoted_fence_declares_no_outcome(monkeypatch, capsys):
    mod = _load()
    body = (
        "<!-- m -->\n- **span=aaaaaaaaaaa1** rebutted\n> ```\n> Outcome: fixed in abc\n> ```\n"
        "> Outcome: rebutted, by design.\n"
    )
    assert mod.declared_outcome(body) == "rebutted"
    assert mod.declared_outcome("<!-- m -->\ntitle\n> ```\n> Outcome: fixed in abc\n> ```\n") == ""
    comments = []
    for i in range(4):
        text = (
            "<!-- ai-review-disposition target=gpt head={} -->\n"
            "**rebutted** span=aaaaaaaaaaa1\n> ```\n> Outcome: fixed in abc\n> ```\n"
            "> Outcome: rebutted, by design.\n"
        ).format(HEADS[i])
        comments.append({"id": i + 1, "body": text, "user": {"login": "writer"}})
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0
    assert "SEAM" not in capsys.readouterr().out
    assert _fix_counts(mod) == {}


def test_a_short_and_a_full_spelling_of_one_head_are_one_round(monkeypatch, capsys):
    mod = _load()
    full = "abcdef0123456789abcdef0123456789abcdef01"
    comments = [
        _disp(1, "gpt", full, "aaaaaaaaaaa1"),
        _disp(2, "gpt", full[:7], "aaaaaaaaaaa1"),
        _disp(3, "opus", full[:10], "bbbbbbbbbbb1"),
        _disp(4, "gpt", HEADS[1], "cccccccccc21"),
        _disp(5, "gpt", HEADS[2], "cccccccccc22"),
        _disp(6, "gpt", HEADS[3], "cccccccccc23"),
    ]
    _wire(mod, monkeypatch, comments)
    # four judged heads, none repeating a span: no seam, and the 5th round is not a cadence round
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0
    out = capsys.readouterr().out
    assert "- round 0 — head abcdef012345 — 3 disposition(s)" in out
    assert "next round: 4" in out and "SEAM" not in out
    assert _fix_counts(mod) == {
        "aaaaaaaaaaa1": 1,
        "bbbbbbbbbbb1": 1,
        "cccccccccc21": 1,
        "cccccccccc22": 1,
        "cccccccccc23": 1,
    }


def test_two_different_heads_with_a_shared_short_prefix_stay_two_rounds(monkeypatch):
    mod = _load()
    comments = [
        _disp(1, "gpt", "abcdef0" + "1" * 33, "aaaaaaaaaaa1"),
        _disp(2, "gpt", "abcdef1" + "1" * 33, "aaaaaaaaaaa1"),
    ]
    _wire(mod, monkeypatch, comments)
    assert _fix_counts(mod) == {"aaaaaaaaaaa1": 2}


# Fence tracking: the opening character and length are remembered, and only a
# matching fence closes the block.
def _outcome(*lines: str) -> str:
    return _load().declared_outcome("<!-- m -->\n" + "\n".join(lines) + "\n")


def test_a_backtick_fence_inside_a_tilde_block_does_not_close_it():
    # the evidence block is ~~~markdown; the ``` inside it is content, so the quoted
    # fixed line stays evidence and the declared outcome is the later rebuttal
    assert (
        _outcome(
            "~~~markdown", "```", "> Outcome: fixed in abc", "```", "~~~", "> Outcome: rebutted."
        )
        == "rebutted"
    )
    assert _outcome("~~~markdown", "```", "> Outcome: fixed in abc", "```", "~~~") == ""


def test_a_tilde_fence_inside_a_backtick_block_does_not_close_it():
    assert _outcome(
        "```", "~~~", "> Outcome: fixed in abc", "~~~", "```", "> Outcome: rebutted."
    ) == ("rebutted")


def test_a_shorter_fence_does_not_close_a_longer_one():
    assert (
        _outcome("````", "```", "> Outcome: fixed in abc", "```", "````", "> Outcome: rebutted.")
        == "rebutted"
    )
    assert (
        _outcome("~~~~~", "~~~", "> Outcome: fixed in abc", "~~~", "~~~~~", "> Outcome: x.") == "x"
    )


def test_a_longer_closing_fence_closes_and_text_after_it_is_read():
    assert _outcome("```", "> Outcome: fixed in abc", "`````", "> Outcome: rebutted.") == "rebutted"


def test_a_closing_fence_carries_no_info_string_and_at_most_three_spaces_of_indent():
    # "```text" does not close a ``` block; four spaces of indent do not either
    assert _outcome("```", "```text", "> Outcome: fixed in abc", "```", "> Outcome: rebutted.") == (
        "rebutted"
    )
    assert _outcome("```", "    ```", "> Outcome: fixed in abc", "   ```", "> Outcome: x.") == "x"


def test_inline_code_with_backticks_and_deep_indent_are_not_fence_openers():
    assert _outcome("```inline``` text", "> Outcome: fixed in abc") == "fixed"
    assert _outcome("    ```", "> Outcome: fixed in abc") == "fixed"
    assert _outcome("   ```", "> Outcome: fixed in abc") == ""


def test_fences_are_tracked_through_blockquote_markers():
    assert (
        _outcome(
            "> ~~~markdown",
            "> ```",
            "> Outcome: fixed in abc",
            "> ```",
            "> ~~~",
            "> Outcome: rebutted.",
        )
        == "rebutted"
    )
    assert _outcome(">> ```", ">> Outcome: fixed in abc", ">> ```", "> Outcome: rebutted.") == (
        "rebutted"
    )


def test_a_block_that_never_closes_hides_what_follows_so_it_counts_nothing():
    assert _outcome("```", "> Outcome: fixed in abc") == ""


def test_a_nested_fence_in_evidence_is_not_a_false_fix_in_the_rounds_view(monkeypatch, capsys):
    mod = _load()
    comments = []
    for i in range(4):
        text = (
            "<!-- ai-review-disposition target=gpt head={} -->\n"
            "**rebutted** span=aaaaaaaaaaa1\n~~~markdown\n```\n- **fixed** quoted\n"
            "> Outcome: fixed in abc\n```\n~~~\n> Outcome: rebutted, by design.\n"
        ).format(HEADS[i])
        comments.append({"id": i + 1, "body": text, "user": {"login": "writer"}})
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0
    assert "SEAM" not in capsys.readouterr().out
    assert _fix_counts(mod) == {}


# Line ends: only CR, LF and CRLF end a line.
def test_a_separator_inside_a_title_does_not_start_an_outcome_line():
    body = "<!-- m -->\ntitle with a break\u2028> Outcome: fixed in abc\n> Outcome: rebutted.\n"
    assert _load().declared_outcome(body) == "rebutted"
    assert _load().declared_outcome("<!-- m -->\r\ntitle\r\n> Outcome: fixed in abc\r\n") == "fixed"


def test_the_outcome_label_may_be_bold():
    for line in ("> **Outcome:** fixed in abc", "> **Outcome**: fixed in abc", ">Outcome:\tfixed"):
        assert _outcome("title", line) == "fixed"


# The same fence class in diff_signals.py (the `## What changed` word limit).
def _signals() -> ModuleType:
    return load_skill_script("prepare_pr_diff_signals_fences", SCRIPT.parent / "diff_signals.py")


def _what_changed(*lines: str) -> str:
    body = "## What changed\n" + "\n".join(lines) + "\n## Tests\n\nx\n"
    return _signals()._what_changed_unfenced(body)


def test_what_changed_drops_a_tilde_block_with_a_backtick_fence_inside():
    text = _what_changed("keep one", "~~~", "```", "drop me", "```", "~~~", "keep two")
    assert "keep one" in text and "keep two" in text
    assert "drop me" not in text and "~~~" not in text


def test_what_changed_drops_a_four_backtick_block_holding_a_three_backtick_line():
    text = _what_changed(
        "keep one", "````", "```", "drop me", "```", "still dropped", "````", "keep two"
    )
    assert "drop me" not in text and "still dropped" not in text
    assert "keep one" in text and "keep two" in text


def test_what_changed_keeps_text_after_a_fence_that_never_closes_as_prose():
    text = _what_changed("keep one", "```", "counted as prose", "also counted")
    assert "counted as prose" in text and "also counted" in text


def test_what_changed_ignores_inline_code_and_deep_indent_as_openers():
    text = _what_changed("```inline``` words", "kept", "    ```", "also kept")
    assert "kept" in text and "also kept" in text


def test_what_changed_closing_fence_needs_the_same_character_and_no_info():
    text = _what_changed("```", "~~~", "dropped", "```lang", "dropped too", "```", "kept")
    assert "dropped" not in text and "kept" in text


# Printed labels and counts are computed from the predicate they name.
def test_recurring_spans_lists_every_span_answered_in_three_rounds_whatever_the_answers(
    monkeypatch, capsys
):
    mod = _load()
    comments = [_disp(i + 1, "gpt", HEADS[i], "aaaaaaaaaaa1", word="rebutted") for i in range(4)]
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0, "rebuttals alone never make it due"
    out = capsys.readouterr().out
    assert "recurring spans (≥3 rounds):\n  aaaaaaaaaaa1 ×4\n" in out
    assert "only fixes make a retrospective due" in out
    assert "recurring spans (≥3 rounds): none" not in out


def test_recurring_spans_counts_rounds_not_records_and_skips_a_span_below_the_threshold(
    monkeypatch, capsys
):
    mod = _load()
    comments = [
        _disp(1, "gpt", HEADS[0], "aaaaaaaaaaa1", word="rebutted"),
        _disp(2, "opus", HEADS[0], "aaaaaaaaaaa1", word="deferred"),
        _disp(3, "gpt", HEADS[1], "aaaaaaaaaaa1", word="fixed"),
        _disp(4, "gpt", HEADS[2], "aaaaaaaaaaa1", word="rebutted"),
        _disp(5, "gpt", HEADS[0], "bbbbbbbbbbb1", word="rebutted"),
        _disp(6, "gpt", HEADS[1], "bbbbbbbbbbb1", word="rebutted"),
        _disp(7, "gpt", HEADS[3], "cccccccccc21", word="rebutted"),
    ]
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 0
    out = capsys.readouterr().out
    assert "  aaaaaaaaaaa1 ×3\n" in out, "three rounds, four records"
    assert "bbbbbbbbbbb1 ×" not in out, "two rounds is below the threshold"


def test_recurring_spans_says_none_only_when_no_span_reaches_the_threshold(monkeypatch, capsys):
    mod = _load()
    comments = [_disp(i + 1, "gpt", HEADS[i], "aaaaaaaaaaa1", word="rebutted") for i in range(2)]
    comments.append(_disp(9, "gpt", HEADS[2], "bbbbbbbbbbb1", word="rebutted"))
    _wire(mod, monkeypatch, comments)
    mod.rounds_view("o/r", 7, "f" * 40, {})
    out = capsys.readouterr().out
    assert "recurring spans (≥3 rounds): none" in out and "only fixes" not in out


def test_the_threshold_in_the_label_is_the_constant_the_list_uses(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "RETRO_EVERY", 2)
    comments = [_disp(i + 1, "gpt", HEADS[i], "aaaaaaaaaaa1", word="rebutted") for i in range(2)]
    _wire(mod, monkeypatch, comments)
    mod.rounds_view("o/r", 7, "f" * 40, {})
    assert "recurring spans (≥2 rounds):\n  aaaaaaaaaaa1 ×2\n" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("fixes", "kind", "ordinal"),
    [(2, "REPEAT", "3rd"), (3, "REPEAT", "4th"), (4, "LIMIT", "5th"), (5, "LIMIT", "6th")],
)
def test_the_seam_line_states_the_ordinal_of_the_next_fix_it_counted(
    monkeypatch, capsys, fixes, kind, ordinal
):
    mod = _load()
    comments = [_disp(i + 1, "gpt", HEADS[i], "aaaaaaaaaaa1") for i in range(fixes)]
    _wire(mod, monkeypatch, comments)
    assert mod.rounds_view("o/r", 7, "f" * 40, {}) == 30
    out = capsys.readouterr().out
    rounds = ", ".join(str(i) for i in range(fixes))
    assert (
        f"SEAM {kind} span=aaaaaaaaaaa1 fixed in rounds {rounds}. The next fix would be its {ordinal}"
        in out
    )
    assert out.count("SEAM ") == 1


def test_nth_covers_the_teens_and_larger_numbers():
    nth = _load()._nth
    assert [nth(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st",
    ]  # fmt: skip


# One fence reader: _review_contract.py. The scripts import it and keep no copy.
SCRIPTS_DIR = SCRIPT.parent


def _contract() -> ModuleType:
    return load_skill_script("prepare_pr_contract_fences", SCRIPTS_DIR / "_review_contract.py")


def test_no_prepare_pr_script_keeps_its_own_fence_parser():
    for name in ("pr_findings.py", "pr_status.py", "diff_signals.py"):
        src = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
        assert "`{3,}" not in src and "FENCE_OPEN_RE = re.compile" not in src, name
    assert "_review_contract" in (SCRIPTS_DIR / "diff_signals.py").read_text(encoding="utf-8")
    status = (SCRIPTS_DIR / "pr_status.py").read_text(encoding="utf-8")
    assert "_fence_opening = _review_contract.fence_opening" in status
    assert "_is_closing_fence = _review_contract.is_closing_fence" in status


def test_the_shared_helpers_pin_character_length_indent_and_info_string():
    c = _contract()
    assert c.fence_opening("```") == ("`", 3) and c.fence_opening("~~~~ md") == ("~", 4)
    assert c.fence_opening("   ```py") == ("`", 3)
    assert c.fence_opening("    ```") is None and c.fence_opening("```a`b") is None
    assert c.fence_opening("``") is None and c.fence_opening("text ```") is None
    assert c.is_closing_fence("```", "`", 3) and c.is_closing_fence("`````", "`", 3)
    assert c.is_closing_fence("   ```  ", "`", 3) and c.is_closing_fence("```\r", "`", 3)
    assert not c.is_closing_fence("``", "`", 3) and not c.is_closing_fence("~~~", "`", 3)
    assert not c.is_closing_fence("```x", "`", 3) and not c.is_closing_fence("    ```", "`", 3)


def test_the_shared_line_readers_agree_with_the_scripts_that_use_them():
    c = _contract()
    text = "a\r\n~~~\n```\nb\n```\n~~~\n> ```\n> c\n> ```\nd\u2028e\rf"
    assert list(c.unfenced_lines(text)) == ["a", "> ```", "> c", "> ```", "d\u2028e", "f"]
    assert list(c.unfenced_lines(text, quoted=True)) == ["a", "d\u2028e", "f"]
    assert c.split_lines("x\r\ny\nz\rw\u2028v") == ["x", "y", "z", "w\u2028v"]
    assert c.drop_fenced_blocks("k1\n```\ndrop\n```\nk2") == "k1\nk2"
    assert c.drop_fenced_blocks("k1\n```\nnever closed") == "k1\n```\nnever closed"
    assert (
        _signals()._what_changed_unfenced("## What changed\nk1\n~~~\n```\nx\n```\n~~~\nk2\n## T\n")
        == "k1\nk2\n"
    )
