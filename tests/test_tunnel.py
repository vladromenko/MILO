from milo_next import tunnel


def test_tunnels_use_low_latency_without_relaxing_host_checks(monkeypatch):
    monkeypatch.setattr(tunnel, "settings", lambda: {
        "edge_user": "test-user", "edge_host": "test-host.local",
    })
    args = tunnel.ssh_args()
    assert args[:2] == ["ssh", "-T"]
    assert args[-1] == "test-user@test-host.local"
    for option in ("IPQoS=lowdelay", "BatchMode=yes", "ConnectTimeout=5",
                   "StrictHostKeyChecking=yes", "ExitOnForwardFailure=yes",
                   "ServerAliveInterval=2", "ServerAliveCountMax=2"):
        assert args[args.index(option) - 1] == "-o"
