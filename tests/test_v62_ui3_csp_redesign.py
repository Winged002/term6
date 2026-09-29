from __future__ import annotations

from pathlib import Path

from term5.ui.web_assets import HTML, CSS, JS


def test_ui3_workspace_is_csp_safe_without_inline_style_positioning():
    assert 'style=' not in HTML
    assert 'style=' not in JS
    assert '.style.' not in JS
    assert 'foreignObject' in JS
    assert 'class="spatial-svg' in JS
    assert 'x="${Math.round(x)}"' in JS
    source = (Path(__file__).resolve().parents[1] / 'term5/ui/web.py').read_text(encoding='utf-8')
    assert "style-src 'self'" in source
    assert "'unsafe-inline'" not in source


def test_ui3_main_coordinator_inspector_is_populated_by_default():
    assert "if(!state.selection)setSelection('central','central')" in JS
    for marker in ['Central coordinator', 'Current coordination', 'Recent objectives', 'Recent handoffs', 'Active objectives']:
        assert marker in JS
    assert 'central-overview' in CSS
    assert 'inspector-symbol' in CSS


def test_ui3_icons_have_explicit_centering_rules():
    for marker in [
        '.compact-rail .rail-nav button', '.icon-dock button', '.workspace-lenses button',
        'place-items:center!important', '.nav-icon svg', '.project-symbol svg',
    ]:
        assert marker in CSS


def test_ui3_inbox_is_attention_first_and_grouped():
    for marker in ['inbox-modern-layout', 'Priority feed', 'Attention', 'inbox-summary-strip']:
        assert marker in HTML
    for marker in ['Needs you', 'Coordination', 'Recent updates', 'modern-attention-row', 'inbox-group']:
        assert marker in JS + CSS


def test_ui3_operations_is_restructured_as_command_center():
    for marker in [
        'ops-command-grid', 'Needs attention', 'Evidence console', 'Collect evidence',
        'modern-release-list', 'modern-ops-list',
    ]:
        assert marker in HTML + CSS
