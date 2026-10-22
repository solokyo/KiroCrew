"""The gateway's own checks of a saved crewmate page (dashboard_page_checks).

Both come from crewmate pages seen on a pod: one bound its item list and a time to
text cells (raw ``[]`` and ISO), and one set light-only text colours, so its page was
pale on white in light mode -- with page verification skipped because the host had
no browser.
"""

from __future__ import annotations

from typing import Any

from kiro_crew.dashboard.handlers import dashboard_page_checks as checks


def _package(html: str, types: dict[str, Any], theme: dict[str, Any] | None = None) -> dict:
    return {
        "model": {"types": types},
        "view": {"html": html},
        "theme": theme if theme is not None else {"tokens": {}},
    }


_TYPES = {
    "work_items": {"type": "array", "source": {"fold": "work", "path": "items"}},
    "last_active": {"type": "timestamp", "source": {"fold": "work", "path": "last_entry_at"}},
    "headline": {"type": "text", "source": {"agentic": True}},
}


def test_a_list_and_a_time_bound_to_text_cells_are_named():
    html = (
        '<p data-dashboard-field="work_items"></p>'
        '<p data-dashboard-field="last_active"></p>'
        '<p data-dashboard-field="headline"></p>'
    )
    warnings = checks.shape_warnings(_package(html, _TYPES))
    assert len(warnings) == 2
    assert warnings[0].startswith("last_active is timestamp")
    assert warnings[1].startswith("work_items is array")
    assert checks.RENDERING["array"] in warnings[1]


def test_a_list_read_by_the_page_script_is_not_named():
    html = "<ul id=items></ul><script>window.kirocrew.subscribe('work_items', draw)</script>"
    assert checks.shape_warnings(_package(html, _TYPES)) == []


def test_the_rendering_note_covers_each_type_a_text_cell_cannot_draw():
    assert set(checks.page_rendering()) == {"array", "object", "timestamp"}


def test_light_only_text_colours_are_flagged():
    """Fixed light text and a translucent surface, the background left to the host."""
    light_only = {
        "tokens": {
            "--kc-fg": "#e8ece7",
            "--kc-fg-muted": "#95a08f",
            "--kc-surface": "rgba(255,255,255,0.04)",
        }
    }
    assert checks.theme_warnings(_package("<p></p>", {}, light_only)) == [checks.THEME_ONE_SCHEME]


def test_a_fixed_board_with_fixed_text_reads_in_both_schemes():
    """A chalkboard: dark board and chalk text, both fixed, so no warning."""
    css = ".board{background:#20262a} .board h1{color:#f4f1e8}"
    assert checks.theme_warnings(_package("<p></p>", {}, {"tokens": {}, "css": css})) == []


def test_theme_variables_or_both_schemes_are_left_alone():
    plain = {"tokens": {}, "css": ".v{color:var(--text)} .b{background:var(--bg)}"}
    assert checks.theme_warnings(_package("<p></p>", {}, plain)) == []
    both = {
        "tokens": {},
        "css": ".v{color:#222} @media (prefers-color-scheme: dark){.v{color:#eee}}",
    }
    assert checks.theme_warnings(_package("<p></p>", {}, both)) == []


def test_a_one_scheme_colour_in_the_page_style_counts_too():
    html = "<style>.v{color:#f5f5f5}</style><p class=v></p>"
    assert checks.theme_warnings(_package(html, {})) != []


def test_a_skipped_run_note_says_nothing_was_checked():
    note = checks.skipped_note("no browser on this host")
    assert "NOT run" in note and "no browser on this host" in note
    assert "no reason given" in checks.skipped_note("")


def test_a_fixed_fallback_text_colour_over_the_host_background_is_flagged():
    """A pod crewmate's page: text from var(--kc-fg, #e8e8e8), which nothing sets, on the
    host's own background, beside small dots with a fixed fill. The dots are not the
    page's background, so the pale text still reads pale on white in light mode."""
    css = (
        ".kc-wrap{color:var(--kc-fg,#e8e8e8);padding:20px}"
        ".kc-card{background:var(--kc-surface,rgba(128,128,128,.07))}"
        ".kc-dot-met{background:var(--kc-success,#4caf50)}"
    )
    html = f"<style>{css}</style><div class=kc-wrap></div>"
    theme = {"tokens": {"--kc-accent": "#4caf50"}}
    assert checks.THEME_ONE_SCHEME in checks.theme_warnings(_package(html, {}, theme))


def test_a_host_variable_with_a_fallback_follows_the_scheme():
    css = ".v{color:var(--text,#e6e6e6)} .dot{background:#4caf50}"
    assert checks.theme_warnings(_package(f"<style>{css}</style>", {})) == []


def test_a_token_the_page_reads_is_resolved_to_its_value():
    css = ".v{color:var(--my-ink)} .p{background:var(--my-paper)}"
    fixed_text_only = {"tokens": {"--my-ink": "#f4f4f4"}}
    assert checks.theme_warnings(_package(f"<style>{css}</style>", {}, fixed_text_only)) != []
    both = {"tokens": {"--my-ink": "#f4f4f4", "--my-paper": "#111111"}}
    css_on_page = ".v{color:var(--my-ink)} body{background:var(--my-paper)}"
    assert checks.theme_warnings(_package(f"<style>{css_on_page}</style>", {}, both)) == []


def test_a_fixed_page_background_with_host_text_is_flagged():
    css = "body{background:#101010} .v{font-weight:600}"
    assert checks.theme_warnings(_package(f"<style>{css}</style>", {})) == [checks.THEME_ONE_SCHEME]


def test_a_surface_the_page_paints_under_scheme_text_is_flagged():
    """A pod crewmate's page in light mode: its theme token pinned ``--accent-subtle``
    to a dark value, a positioned band painted a gradient from it, and the section
    labels laid over the band kept the tab's ``var(--muted)`` -- grey on dark navy."""
    css = (
        ".band{position:absolute;left:0;right:0;bottom:0;height:34%;"
        "background:linear-gradient(180deg,var(--accent-subtle),var(--bg-elevated))}"
        ".sub{color:var(--muted)}"
    )
    pinned = {"tokens": {"--accent-subtle": "#1c3a4f"}}
    out = checks.theme_warnings(_package(f"<style>{css}</style>", {}, pinned))
    assert checks.PAINTED_SURFACE.format(selectors=".band") in out
    # The same band from the tab's own variables follows the scheme: nothing to say.
    assert checks.theme_warnings(_package(f"<style>{css}</style>", {})) == []
    # So does a token written for both schemes.
    both = {"tokens": {"--accent-subtle": "light-dark(#e6f0f5, #1c3a4f)"}}
    assert checks.theme_warnings(_package(f"<style>{css}</style>", {}, both)) == []


def test_an_accent_token_on_a_text_mark_is_not_a_one_scheme_colour():
    """A pod crewmate's page: its own amber accent token, used for a marker glyph and
    small dots on the tab's surfaces, read alike in both schemes."""
    css = ".det>summary:before{color:var(--accent)} .dot{background:var(--accent)}"
    theme = {"tokens": {"--accent": "#f0b05c", "--accent-subtle": "rgba(240,176,92,0.16)"}}
    assert checks.theme_warnings(_package(f"<style>{css}</style>", {}, theme)) == []


def test_text_inside_a_painted_surface_in_scheme_colours_is_flagged():
    css = ".strip{background:#0d2233} .strip h2{color:var(--muted)}"
    out = checks.theme_warnings(_package(f"<style>{css}</style>", {}))
    assert checks.PAINTED_SURFACE.format(selectors=".strip") in out


def test_a_painted_surface_with_its_own_text_colour_or_a_small_mark_is_fine():
    owned = ".strip{background:#0d2233} .strip h2{color:#e8f0f4}"
    assert checks.theme_warnings(_package(f"<style>{owned}</style>", {})) == []
    # A layer whose own text is fixed is owned, even beside scheme text elsewhere.
    layer = (
        ".sky{position:absolute;inset:0;background:#0d2233} .sky h2{color:#e8f0f4}"
        " body{background:var(--bg)} .note{color:var(--muted)}"
    )
    out = checks.theme_warnings(_package(f"<style>{layer}</style>", {}))
    assert not [w for w in out if "paints its own fixed background" in w]
    mark = ".pin{position:absolute;width:10px;height:10px;background:#e33} .v{color:var(--text)}"
    assert checks.theme_warnings(_package(f"<style>{mark}</style>", {})) == []
    dot = ".dot{background:#4caf50} .v{color:var(--text)}"
    assert checks.theme_warnings(_package(f"<style>{dot}</style>", {})) == []


def test_a_var_the_tab_never_defines_with_a_fixed_fallback_is_flagged():
    """The light-mode page that read as pale grey on white: every colour was a var()
    the tab never sets, so each one drew its dark-scheme fallback in both schemes --
    even beside a fixed accent token that made the one-scheme check look satisfied."""
    css = (
        ".kc-wrap{color:var(--kc-fg,#e8e8e8)}"
        ".kc-card{border:1px solid var(--kc-border,rgba(128,128,128,.3))}"
        ".kc-num{color:var(--text)}"
    )
    out = checks.theme_warnings(_package(f"<style>{css}</style>", {}))
    (line,) = [w for w in out if "is not a variable" in w]
    assert line.startswith("--kc-fg, --kc-border is not a variable")
    assert "--text" in line and "--bg" in line  # names the ones that work


def test_a_var_the_page_or_its_tokens_define_is_not_flagged():
    own = ":root{--kc-fg:#111} @media (prefers-color-scheme: dark){:root{--kc-fg:#eee}}"
    css = own + ".v{color:var(--kc-fg,#e8e8e8)}"
    assert checks.theme_warnings(_package(f"<style>{css}</style>", {})) == []
    tokens = {"tokens": {"--kc-fg": "var(--text)"}}
    css2 = ".v{color:var(--kc-fg,#e8e8e8)}"
    out = checks.theme_warnings(_package(f"<style>{css2}</style>", {}, tokens))
    assert not [w for w in out if "is not a variable" in w]


def test_a_host_var_with_a_fallback_and_an_unfixed_fallback_are_fine():
    css = ".v{color:var(--text,#e6e6e6)} .w{color:var(--kc-x,var(--muted))}"
    assert checks.theme_warnings(_package(f"<style>{css}</style>", {})) == []


def test_an_inline_style_var_counts_too():
    html = '<p style="color:var(--my-ink,#eeeeee)">x</p>'
    out = checks.theme_warnings(_package(html, {}))
    assert any(w.startswith("--my-ink is not a variable") for w in out)


def test_the_host_stylesheet_falls_back_per_scheme_never_to_dark_only():
    """Read before the tab's variables land (or with none): the body must not be pale
    text on a dark-only fallback."""
    from kiro_crew import dashboard_frame

    css = dashboard_frame._HOST_CSS
    assert "color: var(--text, light-dark(" in css
    assert "background: var(--bg, light-dark(" in css
    assert "color-scheme: light dark" in css


def test_the_skill_names_only_variables_the_tab_sets():
    import re
    from pathlib import Path

    skill = Path(__file__).resolve().parents[1] / "src/kiro_crew/builtin_skills/dashboard/SKILL.md"
    text = skill.read_text("utf-8")
    section = text[text.index("### A look that belongs to the work") :]
    section = section[: section.index("\n## ")]
    named = set(re.findall(r"`(--[\w-]+)`", section))
    assert {"--bg", "--text", "--card", "--border", "--accent"} <= named
    assert named <= checks.HOST_THEME_VARS
    assert "prefers-reduced-motion" in section and "the motion is off" in section
    # No borrowed look unless the user asks for a style; values drawn by kind.
    assert "add\none only when the user asks for a style" in section
    assert "Draw each value as the kind of information it is" in section
    # The layered build is offered as an optional idea, never a requirement.
    assert "An idea you may use or ignore" in section
    # Principles only: no named theme, motif, layout or effect is prescribed.
    low = section.lower()
    for word in (
        "cyberpunk",
        "neon",
        "2077",
        "rain",
        "weather",
        "lantern",
        "sea ",
        "lane",
        "track",
        " map",
        "hero",
        "motif",
        "palette",
        "glitch",
        "scene",
        "theme of your own",
    ):
        assert word not in low, word


def test_the_skill_defaults_to_live_work_not_the_goal():
    from pathlib import Path

    skill = Path(__file__).resolve().parents[1] / "src/kiro_crew/builtin_skills/dashboard/SKILL.md"
    text = skill.read_text("utf-8")
    section = text[text.index("### A page that updates itself") :]
    section = section[: section.index("### A look that belongs to the work")]
    table = section[: section.index("Your goal and milestones")]
    assert "`goal`" not in table
    for fold in ("`work`", "`workstreams`", "`subagents`", "`tools`", "`timeline`", "`waiting`"):
        assert fold in table, fold
    assert "Profile tab" in section


def test_rules_are_read_in_one_pass_including_nested_and_a_long_brace_free_tail():
    css = "@media (min-width: 1px) { .a { color: #fff } } body{background:#000} .b{}"
    assert checks._rules(css) == [
        (" .a ", " color: #fff "),
        (" body", "background:#000"),
        (" .b", ""),
    ]
    # An inlined source map is one long run with no brace; a backtracking pattern spent
    # time quadratic in it, which at the view cap froze the gateway loop.
    tail = "/*# sourceMappingURL=data:application/json;base64," + "Q" * 1_000_000 + " */"
    assert checks._rules(f".a{{color:#fff}}{tail}") == [(".a", "color:#fff")]
    checks.theme_warnings({"view": {"html": f"<style>{tail}</style>"}, "theme": {"css": tail}})


def test_style_bodies_and_alpha_are_read_in_one_pass():
    html = "<STYLE media=x>.a{color:#fff}</style><p></p><style>.b{}</style>"
    assert checks._style_bodies(html) == ".a{color:#fff} .b{}"
    # Many unclosed <style> tags, and a long run inside one colour function.
    checks._style_bodies("<style>" * 200_000)
    assert checks._opaque("rgba(0, 0, 0, 0.04)") is False
    assert checks._opaque("rgb(0 0 0 / 30%)") is False
    assert checks._opaque("rgba(0, 0, 0, 0.9)") is True
    assert checks._opaque("#000") is True
    assert checks._opaque("rgba(" + "0," * 200_000) is True


def test_a_var_with_a_long_run_of_spaces_is_read_in_linear_time():
    # A backtracking \s* before the fallback spent time quadratic in the spaces.
    css = ".a{color: var(--a," + " " * 400_000 + "}"
    checks.theme_warnings({"view": {"html": f"<style>{css}</style>"}, "theme": {}})
    assert checks._resolve("var(--nope,   #fff )", {}) == "#fff"


def _skill_text() -> str:
    from pathlib import Path

    skill = Path(__file__).resolve().parents[1] / "src/kiro_crew/builtin_skills/dashboard/SKILL.md"
    return skill.read_text("utf-8")


def test_the_glance_section_asks_questions_and_sets_no_layout():
    text = _skill_text()
    section = text[text.index("## 3. What a glance should answer") :]
    section = section[: section.index("\n## ")]
    for q in ("Where does the work stand?", "What is running right now?", "What is stuck?"):
        assert q in section, q
    assert "What needs me?" in section
    # The two rules that hold whatever the design.
    assert "never hidden" in section and "the page says so" in section
    # No layout hard limits pull the page back to a grid of tiles.
    for gone in ("1280", "no scroll", "Numbers over words", "Hard limits", "warm colour"):
        assert gone not in section, gone
    assert "manager view" not in text.lower()


def _three_surfaces() -> dict[str, str]:
    """The skill, the first-build line and the dashboard_save view text."""
    from kiro_crew import mcp_panel
    from kiro_crew.dashboard.goal_reminder import DASHBOARD_BUILD_LINE

    tools = {t["name"]: t for t in mcp_panel._list_tools()}
    view = tools["dashboard_save"]["inputSchema"]["properties"]["view"]["description"]
    return {"skill": _skill_text(), "build line": DASHBOARD_BUILD_LINE, "tool": view}


def test_each_surface_teaches_drawing_by_kind_and_plain_true_words():
    for where, text in _three_surfaces().items():
        flat = " ".join(text.split())
        assert "kind of information it is" in flat, where
        for kind in ("chart", "progress", "side by side", "standing out", "feed", "a count"):
            assert kind in flat or kind.replace("standing out", "stands out") in flat, (where, kind)
        assert (
            "agree with the folded numbers" in flat or "agrees with the folded numbers" in flat
        ), where
        assert "internal id" in flat, where
        assert "phase" in flat, where
        assert "nothing covers anything" in flat.lower(), where
        assert "fit the width" in flat or "fits the width" in flat, where
        assert "borrowed look" in flat, where
        assert (
            "surface the page paints itself" in flat or "surface you paint yourself" in flat
        ), where
    skill = " ".join(_skill_text().split())
    look = skill[skill.index("### A look that belongs to the work") :]
    look = look[: look.index("Colour only with the variables")]
    for rule in (
        "A value that changes over time",
        "Something moving toward done",
        "Parallel workstreams",
        "Something waiting on the user",
        "Recent events",
        "A count is a count",
        "Only the kinds your work has get an element",
        "Rows of detail are welcome below the main elements",
        "Typography and spacing carry the order",
        "Motion where the data changes",
        "What is written agrees with what is drawn",
        "Nothing covers anything",
        "Every row fits the width",
        "nothing runs off the side",
        "A state is not a measure",
    ):
        assert rule in look, rule
    # An event type the page's words do not cover still never shows raw.
    assert "never the raw value" in skill


def test_no_surface_names_a_look():
    """Principles only: no theme, motif, palette, object, layout or animation recipe."""
    import re

    banned = (
        r"boats?", r"sea", r"harbou?r", r"lighthouse", r"waves?", r"lanterns?",
        r"rain", r"neon", r"weather", r"cyberpunk", r"glitch", r"motifs?", r"palettes?",
        r"chalkboard", r"constellation", r"sky", r"garden", r"city", r"orbits?",
        r"lamps?", r"flames?", r"planets?", r"scenes?", r"d3", r"chart\.js",
        r"recharts", r"echarts",
    )  # fmt: skip
    for where, text in _three_surfaces().items():
        for word in banned:
            hit = re.search(rf"\b{word}\b", text, re.I)
            assert hit is None, (where, hit.group(0) if hit else "")


def test_the_gallery_teaches_wiring_not_a_look():
    text = _skill_text()
    section = " ".join(text[text.index("## 7. Pages to start from") :].split())
    assert "how a page declares and binds its fields, not how a page should look" in section


def test_dashboard_types_names_the_rows_a_page_draws():
    from kiro_crew import dashboard_types
    from kiro_crew.crew_log.projection_side_panel import TIMELINE_MOMENT_KEYS
    from kiro_crew.crew_log.projection_subagents import SUBAGENT_ROW_KEYS

    types = {t["name"]: t for t in dashboard_types.describe()["types"]}
    moments = types["timeline"]["shape"]["properties"]["moments"]
    assert moments["row_keys"] == list(TIMELINE_MOMENT_KEYS)
    assert moments["names_the_row"] == "type"
    by_id = types["subagents"]["shape"]["properties"]["by_id"]
    assert by_id["row_keys"] == list(SUBAGENT_ROW_KEYS) and by_id["names_the_row"] == "task"
    # The skill points a page at those names, so it never guesses a key.
    text = _skill_text()
    assert "`row_keys`" in text and "`names_the_row`" in text


def test_the_subagent_row_keys_are_the_keys_the_fold_writes():
    from types import SimpleNamespace

    from kiro_crew.crew_log import projection_subagents as ps

    state = ps._subagents_start()
    entry = SimpleNamespace(
        type="subagent/spawned", data={"agent_id": "a1", "task": "t"}, seq=1, time=1
    )
    ps._subagents_step(state, entry)  # type: ignore[arg-type]
    assert tuple(state["by_id"]["a1"]) == ps.SUBAGENT_ROW_KEYS


def _fonts(css: str, html: str = "<p></p>", tokens: dict[str, Any] | None = None) -> list[str]:
    theme = {"tokens": tokens or {}, "css": css}
    return [w for w in checks.theme_warnings(_package(html, {}, theme)) if "Type check" in w]


def test_two_role_variables_raise_no_type_warning():
    css = "body { font: 14px/1.5 var(--font-sans); } code, .n { font-family: var(--font-mono); }"
    assert _fonts(css) == []
    # A role variable and its own generic families are one family, not two.
    same_role = css + " p { font-family: system-ui, sans-serif; } kbd { font: 12px monospace; }"
    assert _fonts(same_role) == []
    # A page's own variable names no typeface; only what it resolves to could.
    assert _fonts("h1 { font-family: var(--brand-font, var(--font-sans)); }") == []


def test_a_third_family_raises_the_pairing_line():
    css = (
        "body { font-family: var(--font-sans); } h1 { font-family: var(--font-serif); }"
        " code { font-family: ui-monospace, monospace; }"
    )
    (line,) = _fonts(css)
    assert line.startswith("Type check: this page sets 3 font families")
    assert "may read calmer with at most two" in line and "tabular-nums" in line


def test_a_named_typeface_raises_the_named_face_line():
    (line,) = _fonts("h1 { font-family: 'Playfair Display', serif; }")
    assert "playfair display names a typeface" in line
    assert "var(--font-sans)" in line and "unless the user named this face" in line


def test_an_inline_data_font_and_a_style_attribute_count_too():
    css = "@font-face { font-family: 'Brand'; src: url(data:font/woff2;base64,AAAA); }"
    assert any("brand names a typeface" in w for w in _fonts(css))
    html = '<p style="font-family: Georgia, serif">x</p>'
    assert any("georgia names a typeface" in w for w in _fonts("", html))


def test_a_font_token_counts_and_a_shorthand_with_no_family_does_not():
    assert _fonts("", tokens={"--font-display": "Futura"}) != []
    assert _fonts("body { font: inherit; } p { font-family: inherit; }") == []


def test_the_type_check_reads_a_long_value_with_no_nested_repeat():
    """Linear by construction: the declaration pattern holds one repeat whose class
    cannot overlap what comes before it, so a long value is read once."""
    pattern = checks._FONT_DECL.pattern
    assert pattern.count("*") == 2 and ")*" not in pattern and "+)" not in pattern
    assert _fonts("p { font:" + " " * 200_000 + "x }") == []
