import httpx

from cli.__main__ import build_parser
from cli import plugins_cmd


def test_cli_contacts_running_server(monkeypatch, capsys):
    monkeypatch.setenv("TOMO_API_KEY", "tomo_test_key")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"running": True})

    original = httpx.Client
    monkeypatch.setattr(
        plugins_cmd.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)),
    )
    args = build_parser().parse_args(["plugins", "reload", "money"])
    assert plugins_cmd.run(args) == 0
    assert requests[0].url.path == "/api/plugins/money/reload"
    assert requests[0].headers["Authorization"] == "Bearer tomo_test_key"
    assert '"running": true' in capsys.readouterr().out


def test_cli_refuses_remote_plain_http(monkeypatch, capsys):
    monkeypatch.setenv("TOMO_API_KEY", "tomo_test_key")
    args = build_parser().parse_args(
        ["plugins", "--url", "http://remote.example", "list"]
    )
    assert plugins_cmd.run(args) == 1
    assert "HTTPS" in capsys.readouterr().err


def test_cli_outdated_and_update(monkeypatch):
    monkeypatch.setenv("TOMO_API_KEY", "tomo_test_key")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    original = httpx.Client
    monkeypatch.setattr(
        plugins_cmd.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)),
    )
    assert plugins_cmd.run(build_parser().parse_args(["plugins", "outdated"])) == 0
    assert requests[-1].method == "POST"
    assert requests[-1].url.path == "/api/plugins/check-updates"
    assert (
        plugins_cmd.run(build_parser().parse_args(["plugins", "update", "money"])) == 0
    )
    assert requests[-1].url.path == "/api/plugins/money/update"


def test_cli_dependency_sync(monkeypatch):
    monkeypatch.setenv("TOMO_API_KEY", "tomo_test_key")
    requests = []
    original = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"enabled": False})

    monkeypatch.setattr(
        plugins_cmd.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)),
    )
    assert (
        plugins_cmd.run(
            build_parser().parse_args(["plugins", "sync-dependencies", "vehicle_cctv"])
        )
        == 0
    )
    assert requests[0].url.path == "/api/plugins/vehicle_cctv/sync-dependencies"
