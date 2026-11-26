# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for REST :4343, SSH asyncssh PTY, AutoTransport, and MultiApClusterClient."""
from __future__ import annotations

import json
from typing import Any

import asyncssh
import pytest

from custom_components.aruba_instant.adapters.multi_ap_client import (
    AutoTransportClient,
    MultiApClusterClient,
)
from custom_components.aruba_instant.adapters.rest_client import ArubaRestClient
from custom_components.aruba_instant.adapters.ssh_client import ArubaSshClient
from custom_components.aruba_instant.engine.models import ClusterSnapshot
from custom_components.aruba_instant.engine.parsers import parse_clients, parse_summary
from custom_components.aruba_instant.ports.cluster_client import (
    ArubaAuthError,
    ArubaConnectionError,
    ArubaMultiAuthError,
    ArubaReadOnlyCliError,
    ArubaRestApiDisabledError,
    BaseArubaClusterClient,
)
from tests.fixtures.aruba_8_10_samples import (
    IMAGE_CHECK_SUCCESS_EMPTY_XML,
    MULTI_AP_NODE1_CLIENTS,
    MULTI_AP_NODE2_CLIENTS,
    REST_503_DISABLED_BODY,
    REST_503_SESSION_LIMIT_BODY,
    REST_LOGIN_EXPIRED_JSON,
    REST_LOGIN_SUCCESS_JSON,
    SHOW_APS_OUTPUT,
    SHOW_APS_SINGLE_SSH_RAW,
    SHOW_CLIENTS_OUTPUT,
    SHOW_CLIENTS_SSH_RAW,
    SHOW_IMAGE_VERSION_OUTPUT,
    SHOW_NETWORK_OUTPUT,
    SHOW_SUMMARY_OUTPUT,
    SHOW_SUMMARY_STANDALONE_AOS10,
    SHOW_UPGRADE_INFO_OUTPUT,
    SHOW_VERSION_OUTPUT,
)


class _FakeResponse:
    def __init__(
        self,
        status: int,
        body: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def text(self) -> str:
        if isinstance(self._body, str):
            return self._body
        return json.dumps(self._body)

    async def json(self, content_type: Any = None) -> Any:
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


class _FakeSession:
    def __init__(self, post_handlers: list[_FakeResponse], get_handlers: list[_FakeResponse]) -> None:
        self._posts = list(post_handlers)
        self._gets = list(get_handlers)
        self.post_calls: list[tuple[str, Any]] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.post_calls.append((url, kwargs.get("json")))
        return self._posts.pop(0)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.get_calls.append((url, dict(kwargs.get("params") or {})))
        return self._gets.pop(0)


async def test_rest_client_fetch_and_logout_lifecycle() -> None:
    session = _FakeSession(
        post_handlers=[
            _FakeResponse(200, REST_LOGIN_SUCCESS_JSON),
            _FakeResponse(200, {"Status": "Success"}),  # /rest/logout
        ],
        get_handlers=[
            _FakeResponse(200, {"Command output": SHOW_SUMMARY_OUTPUT}),
            _FakeResponse(200, {"Command output": SHOW_NETWORK_OUTPUT}),
            _FakeResponse(200, {"Command output": SHOW_APS_OUTPUT}),
            _FakeResponse(200, {"Command output": SHOW_CLIENTS_OUTPUT}),
        ],
    )
    client = ArubaRestClient(session, "192.0.2.10", "admin", "secret")  # type: ignore[arg-type]
    snap = await client.async_fetch_snapshot()

    assert snap.summary.name == "MainCluster"
    assert len(snap.access_points) == 3
    assert len(snap.clients) == 7
    assert session.get_calls[0][1]["iap_ip_addr"] == "192.0.2.10"
    assert session.get_calls[0][1]["sid"] == "test-session-token-0001"

    await client.async_close()
    assert session.post_calls[-1][0].endswith("/rest/logout")
    assert session.post_calls[-1][1] == {"sid": "test-session-token-0001"}


async def test_rest_client_distinguishes_503_disabled_vs_session_limit() -> None:
    disabled_session = _FakeSession([_FakeResponse(503, REST_503_DISABLED_BODY)], [])
    client1 = ArubaRestClient(disabled_session, "192.0.2.10", "admin", "secret")  # type: ignore[arg-type]
    with pytest.raises(ArubaRestApiDisabledError) as exc1:
        await client1.async_fetch_snapshot()
    assert exc1.value.host == "192.0.2.10"

    limit_session = _FakeSession([_FakeResponse(503, REST_503_SESSION_LIMIT_BODY)], [])
    client2 = ArubaRestClient(limit_session, "192.0.2.10", "admin", "secret")  # type: ignore[arg-type]
    with pytest.raises(ArubaConnectionError):
        await client2.async_fetch_snapshot()


async def test_rest_client_relogins_on_expired_sid() -> None:
    session = _FakeSession(
        post_handlers=[
            _FakeResponse(200, REST_LOGIN_SUCCESS_JSON),
            _FakeResponse(200, {"Status": "Success", "sid": "new-sid-0002"}),
        ],
        get_handlers=[
            _FakeResponse(200, REST_LOGIN_EXPIRED_JSON),
            _FakeResponse(200, {"Command output": SHOW_SUMMARY_OUTPUT}),
            _FakeResponse(200, {"Command output": SHOW_NETWORK_OUTPUT}),
            _FakeResponse(200, {"Command output": SHOW_APS_OUTPUT}),
            _FakeResponse(200, {"Command output": SHOW_CLIENTS_OUTPUT}),
        ],
    )
    client = ArubaRestClient(session, "192.0.2.10", "admin", "secret")  # type: ignore[arg-type]
    snap = await client.async_fetch_snapshot()
    assert snap.summary.name == "MainCluster"
    assert len(session.post_calls) == 2
    assert session.get_calls[-1][1]["sid"] == "new-sid-0002"


async def test_rest_client_auth_and_session_limit_classification() -> None:
    session = _FakeSession([_FakeResponse(401, "Unauthorized")], [])
    client = ArubaRestClient(session, "192.0.2.10", "admin", "bad")  # type: ignore[arg-type]
    with pytest.raises(ArubaAuthError):
        await client.async_fetch_snapshot()

    # Transient empty-sid login failure with session-limit message must raise ConnectionError, not AuthError
    limit_login = _FakeSession(
        [_FakeResponse(200, {"Status": "Failed", "Status-code": 1, "message": "Maximum number of sessions reached"})],
        [],
    )
    client_limit = ArubaRestClient(limit_login, "192.0.2.10", "admin", "secret")  # type: ignore[arg-type]
    with pytest.raises(ArubaConnectionError):
        await client_limit.async_fetch_snapshot()

    # Empty Command output string returns "" rather than leaking raw JSON envelope
    empty_out_session = _FakeSession(
        [_FakeResponse(200, REST_LOGIN_SUCCESS_JSON)],
        [_FakeResponse(200, {"Status": "Success", "Command output": ""})],
    )
    client_empty = ArubaRestClient(empty_out_session, "192.0.2.10", "admin", "secret")  # type: ignore[arg-type]
    assert await client_empty._show_cmd("show clients") == ""


class _FakeSshStream:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.written: list[str] = []
        self._pending_read: str | None = None

    def write(self, data: str) -> None:
        self.written.append(data)

    async def read(self, n: int) -> str:
        if not self._responses:
            return ""
        return self._responses.pop(0)


class _FakeSshProcess:
    def __init__(self, responses: list[str]) -> None:
        self.stdout = _FakeSshStream(responses)
        self.stdin = self.stdout
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeSshConnection:
    def __init__(self, responses: list[str]) -> None:
        self.process = _FakeSshProcess(responses)
        self.create_kwargs: dict[str, Any] = {}
        self.closed = False

    async def create_process(self, **kwargs: Any) -> _FakeSshProcess:
        self.create_kwargs = kwargs
        return self.process

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


async def test_ssh_client_wide_pty_kex_retry_and_enable_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_attempts: list[dict[str, Any]] = []
    fake_conn = _FakeSshConnection(
        [
            "AP-Standalone# ",
            "AP-Standalone# ",  # no paging
            SHOW_SUMMARY_STANDALONE_AOS10 + "\r\nAP-Standalone# ",
            SHOW_NETWORK_OUTPUT + "\r\nAP-Standalone# ",
            SHOW_APS_SINGLE_SSH_RAW,
            SHOW_CLIENTS_SSH_RAW,
        ]
    )

    async def fake_connect(**kwargs: Any) -> _FakeSshConnection:
        connect_attempts.append(kwargs)
        if len(connect_attempts) == 1:
            raise asyncssh.KeyExchangeFailed("legacy kex")  # type: ignore[attr-defined]
        return fake_conn

    monkeypatch.setattr(asyncssh, "connect", fake_connect)

    client = ArubaSshClient("198.51.100.21", "admin", "secret")
    snap = await client.async_fetch_snapshot()
    assert len(connect_attempts) == 2
    assert connect_attempts[0]["known_hosts"] is None
    assert connect_attempts[0]["config"] == ()
    assert "diffie-hellman-group14-sha1" in connect_attempts[1]["kex_algs"]
    assert fake_conn.create_kwargs == {"term_type": "vt100", "term_size": (512, 1000)}
    assert fake_conn.process.stdin.written[0] == "no paging\n"
    assert snap.access_points[0].name == "AP-Standalone"

    # Explicit consent command execution & read-only rejection
    ro_conn = _FakeSshConnection(
        [
            "AP-Central# ",
            "AP-Central(config)# ",
            "% Invalid input detected at '^' marker.\r\nAP-Central(config)# ",
        ]
    )

    async def fake_ro_connect(**kwargs: Any) -> _FakeSshConnection:
        return ro_conn

    monkeypatch.setattr(asyncssh, "connect", fake_ro_connect)
    with pytest.raises(ArubaReadOnlyCliError):
        await client.async_enable_rest_api()


async def test_auto_transport_caches_fallback_and_never_swallows_auth() -> None:
    class _FakeNode(BaseArubaClusterClient):
        def __init__(self, outcome: Any) -> None:
            self.outcome = outcome
            self.calls = 0
            self.closed = False

        async def async_fetch_snapshot(self) -> ClusterSnapshot:
            self.calls += 1
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

        async def async_close(self) -> None:
            self.closed = True

    ssh_snap = ClusterSnapshot(summary=parse_summary(SHOW_SUMMARY_STANDALONE_AOS10))
    rest = _FakeNode(ArubaRestApiDisabledError("disabled", host="192.0.2.10"))
    ssh = _FakeNode(ssh_snap)
    auto = AutoTransportClient(rest, ssh)  # type: ignore[arg-type]

    assert await auto.async_fetch_snapshot() is ssh_snap
    assert auto.active_transport == "ssh"
    # Permanent REST disabled keeps using SSH without hitting REST 503 again
    assert await auto.async_fetch_snapshot() is ssh_snap
    assert rest.calls == 1
    assert ssh.calls == 2

    # When fallback_on_rest_disabled=False (ConfigFlow probe), 503 disabled re-raises
    strict_auto = AutoTransportClient(rest, ssh, fallback_on_rest_disabled=False)  # type: ignore[arg-type]
    with pytest.raises(ArubaRestApiDisabledError):
        await strict_auto.async_fetch_snapshot()

    # Transient ArubaConnectionError recovers back to REST after cooldown
    transient_rest = _FakeNode(ArubaConnectionError("transient timeout", host="192.0.2.10"))
    transient_auto = AutoTransportClient(transient_rest, ssh)  # type: ignore[arg-type]
    assert await transient_auto.async_fetch_snapshot() is ssh_snap
    transient_rest.outcome = ssh_snap
    transient_auto._ssh_cooldown_remaining = 0
    assert await transient_auto.async_fetch_snapshot() is ssh_snap
    assert transient_rest.calls == 2

    bad_rest = _FakeNode(ArubaAuthError("bad auth", host="192.0.2.10"))
    good_ssh = _FakeNode(ssh_snap)
    auto_auth = AutoTransportClient(bad_rest, good_ssh)  # type: ignore[arg-type]
    with pytest.raises(ArubaAuthError):
        await auto_auth.async_fetch_snapshot()
    assert good_ssh.calls == 0


async def test_multi_ap_cluster_client_partial_outage_and_auth_error() -> None:
    class _StubClient(BaseArubaClusterClient):
        def __init__(self, outcome: Any) -> None:
            self.outcome = outcome
            self.closed = False

        async def async_fetch_snapshot(self) -> ClusterSnapshot:
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

        async def async_close(self) -> None:
            self.closed = True

    s1 = ClusterSnapshot(
        summary=parse_summary(SHOW_SUMMARY_STANDALONE_AOS10),
        clients=parse_clients(MULTI_AP_NODE1_CLIENTS),
    )
    s2 = ClusterSnapshot(
        summary=parse_summary(SHOW_SUMMARY_STANDALONE_AOS10),
        clients=parse_clients(MULTI_AP_NODE2_CLIENTS),
    )
    nodes = [
        {"host": "198.51.100.31", "ap_id": "SN1", "name": "AP-1"},
        {"host": "198.51.100.32", "ap_id": "SN2", "name": "AP-2"},
        {"host": "198.51.100.33", "ap_id": "SN3", "name": "AP-3"},
    ]

    # 1 of 3 APs offline -> succeeds with merged clients + offline AP record
    pool = MultiApClusterClient(
        session=None,  # type: ignore[arg-type]
        nodes=nodes,
        site_name="Campus",
        node_clients=[
            _StubClient(s1),
            _StubClient(s2),
            _StubClient(ArubaConnectionError("timeout", host="198.51.100.33")),
        ],
    )
    merged = await pool.async_fetch_snapshot()
    assert len(merged.clients) == 1
    assert merged.clients[0].access_point == "AP-Standalone-2"
    offline_ap = next(ap for ap in merged.access_points if ap.serial == "SN3")
    assert offline_ap.online is False
    await pool.async_close()

    # Auth rejection on AP 2 -> ArubaMultiAuthError naming AP 2
    auth_pool = MultiApClusterClient(
        session=None,  # type: ignore[arg-type]
        nodes=nodes[:2],
        site_name="Campus",
        node_clients=[
            _StubClient(s1),
            _StubClient(ArubaAuthError("rejected", host="198.51.100.32")),
        ],
    )
    with pytest.raises(ArubaMultiAuthError) as exc:
        await auth_pool.async_fetch_snapshot()
    assert exc.value.failed_hosts == ["198.51.100.32"]


async def test_rest_and_ssh_firmware_and_write_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[tuple[str, Any, Any]] = []

    class _FirmwareSession:
        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            posts.append((url, kwargs.get("params"), kwargs.get("json")))
            if url.endswith("/rest/login"):
                return _FakeResponse(200, REST_LOGIN_SUCCESS_JSON)
            return _FakeResponse(200, {"Status": "Success", "Status-code": 0})

        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            if url.endswith("/swarm.cgi"):
                return _FakeResponse(200, IMAGE_CHECK_SUCCESS_EMPTY_XML)
            cmd = (kwargs.get("params") or {}).get("cmd", "")
            if cmd == "show image version":
                return _FakeResponse(200, {"Command output": SHOW_IMAGE_VERSION_OUTPUT})
            if cmd == "show version":
                return _FakeResponse(200, {"Command output": SHOW_VERSION_OUTPUT})
            if cmd == "show upgrade info":
                return _FakeResponse(200, {"Command output": SHOW_UPGRADE_INFO_OUTPUT})
            return _FakeResponse(200, {"Command output": ""})

    client = ArubaRestClient(_FirmwareSession(), "192.0.2.10", "admin", "pw")  # type: ignore[arg-type]
    fw_info, fw_check = await client.async_fetch_firmware_details()
    assert fw_info is not None and fw_info.image_classes == ("Hercules",)
    assert fw_check is not None and fw_check.server_state == "success"

    await client.async_reboot_ap("192.0.2.12")
    await client.async_set_ssid_enabled("Guest_Profile", False)
    await client.async_upgrade_firmware("http://192.0.2.50/img.bin", ap_class="Hercules")
    assert any("/rest/reboot" in u and j["iap_ip_addr"] == "192.0.2.12" for u, _, j in posts)
    assert any(
        "/rest/ssid" in u
        and j.get("iap_ip_addr") == "192.0.2.10"
        and j["ssid-profile"]["disable"] == "yes"
        for u, _, j in posts
    )
    assert any("/rest/os-upgrade" in u and "Hercules-url" in j["upgrade-info"] for u, _, j in posts)

    # SSH reload (y/n) confirmation, drain(), and hostname-resolved local IP guard
    written: list[str] = []

    class _RebootProc:
        def __init__(self) -> None:
            self.step = 0
            self.drained = False

            class _In:
                def write(self_in, data: str) -> None:
                    written.append(data)

                async def drain(self_in) -> None:
                    self.drained = True

            self.stdin = _In()
            self.stdout = type("_Out", (), {"read": self._read})()

        async def _read(self, _n: int) -> str:
            self.step += 1
            if self.step == 1:
                return "AP-Standalone# "
            if self.step == 2:
                return "Do you really want to reset the system(y/n):"
            return ""

        def close(self) -> None:
            return None

    class _RebootConn:
        async def create_process(self, **_kw: Any) -> _RebootProc:
            return _RebootProc()

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    dialed_hosts: list[str] = []

    async def fake_connect(**kwargs: Any) -> _RebootConn:
        dialed_hosts.append(str(kwargs.get("host", "")))
        return _RebootConn()

    monkeypatch.setattr(asyncssh, "connect", fake_connect)
    ssh = ArubaSshClient("ap-standalone.example.test", "admin", "pw")
    ssh._local_ap_ip = "198.51.100.21"
    with pytest.raises(ArubaReadOnlyCliError):
        await ssh.async_reboot_ap("198.51.100.22")
    await ssh.async_reboot_ap("198.51.100.21")
    assert written == ["reload\n", "y\n"]
    assert dialed_hosts[-1] == "ap-standalone.example.test"

    # Rebooting a known peer AP dials that peer directly even when _active_host is another AP
    ssh_cluster = ArubaSshClient("198.51.100.21", "admin", "pw")
    ssh_cluster._active_host = "198.51.100.22"
    ssh_cluster.set_known_peer_ips(["198.51.100.21", "198.51.100.22"])
    await ssh_cluster.async_reboot_ap("198.51.100.21")
    assert dialed_hosts[-1] == "198.51.100.21"

    # MultiApClusterClient reboot routes only to matching node
    called: list[str] = []

    class _StubReboot(BaseArubaClusterClient):
        def __init__(self, tag: str) -> None:
            self.tag = tag

        async def async_fetch_snapshot(self) -> ClusterSnapshot:
            return ClusterSnapshot(summary=parse_summary(SHOW_SUMMARY_STANDALONE_AOS10))

        async def async_close(self) -> None:
            return None

        async def async_reboot_ap(self, ap_ip: str) -> None:
            called.append(f"{self.tag}:{ap_ip}")

    pool = MultiApClusterClient(
        session=None,  # type: ignore[arg-type]
        nodes=[{"host": "198.51.100.31"}, {"host": "198.51.100.32"}],
        site_name="Campus",
        node_clients=[_StubReboot("n1"), _StubReboot("n2")],
    )
    await pool.async_reboot_ap("198.51.100.32")
    assert called == ["n2:198.51.100.32"]


async def test_rest_member_ap_discovery_and_master_failover() -> None:
    """Connecting to a non-Master AP adopts reachable VC VIP or falls back seamlessly to Master."""
    import aiohttp

    master_alive = True
    vip_alive = False

    class _FailoverSession:
        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            if "192.0.2.10:" in url and not vip_alive:
                raise aiohttp.ClientError("192.0.2.10 unreachable VLAN VIP")
            if "192.0.2.12:" in url and "/rest/login" in url:
                return _FakeResponse(
                    503,
                    "<HTML>\n<HEAD><TITLE>503 Service Unavailable</TITLE></HEAD>\n"
                    '<BODY BGCOLOR="#cc9999" TEXT="#000000" LINK="#2020ff" VLINK="#4040cc">\n'
                    "<H4>503 Service Unavailable</H4>\n"
                    "REST API service is available only on Master IAP\n"
                    "</BODY>\n</HTML>",
                )
            if "192.0.2.11:" in url and not master_alive:
                raise aiohttp.ClientError("192.0.2.11 down")
            return _FakeResponse(200, REST_LOGIN_SUCCESS_JSON)

        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            if "192.0.2.10" in url and not vip_alive:
                raise aiohttp.ClientError("192.0.2.10 unreachable VLAN VIP")
            if "192.0.2.12" in url and "/rest/" not in url:
                target = "https://192.0.2.11:4343/" if master_alive else "https://192.0.2.13:4343/"
                return _FakeResponse(302, "", headers={"Location": target})
            if "192.0.2.11:" in url and not master_alive:
                raise aiohttp.ClientError("192.0.2.11 down")
            cmd = (kwargs.get("params") or {}).get("cmd", "")
            if cmd == "show summary":
                out = SHOW_SUMMARY_OUTPUT if master_alive else SHOW_SUMMARY_OUTPUT.replace("192.0.2.11", "192.0.2.13")
                return _FakeResponse(200, {"Command output": out})
            if cmd == "show network":
                return _FakeResponse(200, {"Command output": SHOW_NETWORK_OUTPUT})
            if cmd == "show aps":
                return _FakeResponse(200, {"Command output": SHOW_APS_OUTPUT})
            if cmd == "show clients":
                return _FakeResponse(200, {"Command output": SHOW_CLIENTS_OUTPUT})
            return _FakeResponse(404, "")

    client = ArubaRestClient(_FailoverSession(), "192.0.2.12", "admin", "pw")  # type: ignore[arg-type]
    snap1 = await client.async_fetch_snapshot()
    # Non-destructively probes VC VIP 192.0.2.10 on poll #1; keeps Master 192.0.2.11 when VIP fails
    assert client.active_host == "192.0.2.11"
    assert snap1.summary.conductor_ip == "192.0.2.11"
    assert "192.0.2.10" in client._unusable_vc_ips
    assert client.get_vc_vip_failure_count("192.0.2.10") == 1
    peers = client.get_known_peer_ips()
    assert "192.0.2.11" in peers
    assert "192.0.2.12" in peers
    assert "192.0.2.13" in peers

    # Cooldown ticks down without re-probing; when cooldown expires, second failure increments count to 2
    client._vc_vip_cooldown["192.0.2.10"] = 1
    await client.async_fetch_snapshot()
    assert client.active_host == "192.0.2.11"
    assert client.get_vc_vip_failure_count("192.0.2.10") == 2

    # When cooldown expires and VIP becomes reachable again, client promotes back and clears failure counter
    vip_alive = True
    client._vc_vip_cooldown["192.0.2.10"] = 1
    await client.async_fetch_snapshot()
    assert client.active_host == "192.0.2.10"
    assert client.get_vc_vip_failure_count("192.0.2.10") == 0

    # Reset VIP unreachable state to verify peer-driven Master AP failover when Master 192.0.2.11 goes down
    vip_alive = False
    await client.async_fetch_snapshot()
    assert client.active_host == "192.0.2.11"

    # Poll #5+: Simulate Master 192.0.2.11 going down; fails over via peer 192.0.2.12 -> Master 192.0.2.13
    master_alive = False
    snap2 = await client.async_fetch_snapshot()
    assert client.active_host == "192.0.2.13"
    assert snap2.summary.conductor_ip == "192.0.2.13"

    # Verify reachable VC VIP case promotes immediately on poll #1 and logs out old session
    vip_alive = True
    vip_client = ArubaRestClient(_FailoverSession(), "192.0.2.12", "admin", "pw")  # type: ignore[arg-type]
    await vip_client.async_fetch_snapshot()
    assert vip_client.active_host == "192.0.2.10"
    assert vip_client.get_vc_vip_failure_count("192.0.2.10") == 0
