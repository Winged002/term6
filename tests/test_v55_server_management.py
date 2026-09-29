from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from term5.config import load_config
from term5.ops.server import ServerManager, ServerOpsError
from term5.runtime import AgentRuntime
from term5.ui.web_assets import CSS, JS


def test_alpha3_stable_ui_is_the_5_5_baseline():
    assert '.app{' in CSS
    assert '.sidebar' in CSS
    assert '.drawer' in CSS
    assert 'function route' in JS
    assert JS.count('setTimeout(poll') == 1
    assert 'setInterval(' not in JS
    assert 'Command palette' not in JS


def test_server_status_exposes_bounded_capacity(tmp_path):
    mgr = ServerManager(root=tmp_path, allowed_services=['docker', 'nginx'])
    data = mgr.status()
    assert data['cpu_count'] is None or data['cpu_count'] >= 1
    assert 'memory' in data and 'disk' in data
    assert data['allowed_services'] == ['docker', 'nginx']
    assert isinstance(data['warnings'], list)


def test_server_control_rejects_non_allowlisted_service(tmp_path):
    mgr = ServerManager(root=tmp_path, allowed_services=['nginx'])
    with pytest.raises(ServerOpsError):
        mgr.service_control('ssh', 'restart')


def test_server_service_control_uses_typed_systemctl_argv(tmp_path):
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ['systemctl', 'is-active']:
            return subprocess.CompletedProcess(argv, 0, 'active\n')
        if argv[:2] == ['systemctl', 'is-enabled']:
            return subprocess.CompletedProcess(argv, 0, 'enabled\n')
        return subprocess.CompletedProcess(argv, 0, '')

    mgr = ServerManager(root=tmp_path, allowed_services=['nginx'], runner=fake_runner)
    mgr.cmd.which = lambda _binary: '/usr/bin/fake'
    data = mgr.service_control('nginx', 'restart')
    assert data['ok'] is True
    assert ['systemctl', 'restart', 'nginx'] in calls
    assert all(isinstance(x, list) for x in calls)


def test_5_5_runtime_registers_server_and_5_4_intelligence_tools(tmp_path):
    cfg = load_config(tmp_path)
    runtime = AgentRuntime(cfg)
    names = set(runtime.tools.names())
    for name in {
        'server_status', 'server_ports', 'server_service_status', 'server_journal',
        'server_service_control', 'server_audit', 'production_readiness',
        'application_map', 'improvement_plan', 'browser_status', 'browser_screenshot',
        'vision_inspect', 'vision_compare', 'deployment_deploy', 'deployment_rollback',
    }:
        assert name in names


def test_server_write_is_separately_gated(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.security.allow_server_write is False
    assert cfg.operations.managed_services == ['docker', 'nginx']
    runtime = AgentRuntime(cfg)
    assert runtime.security.capabilities.has('ops.server.read')
    assert not runtime.security.capabilities.has('ops.server.write')
