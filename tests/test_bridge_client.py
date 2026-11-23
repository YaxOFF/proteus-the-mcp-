"""Offline tests for BridgeClient: a fake named-pipe server speaks the MCPBRIDGE line protocol."""
import ctypes as c
import sys
import threading
import time
import uuid

import pytest

from proteus_mcp.bridge_client import BridgeClient, BridgeError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")

if sys.platform == "win32":
    k = c.WinDLL("kernel32", use_last_error=True)
    k.CreateNamedPipeW.argtypes = (c.c_wchar_p, c.c_uint32, c.c_uint32, c.c_uint32, c.c_uint32,
                                   c.c_uint32, c.c_uint32, c.c_void_p)
    k.CreateNamedPipeW.restype = c.c_void_p
    k.ConnectNamedPipe.argtypes = (c.c_void_p, c.c_void_p)
    k.ReadFile.argtypes = (c.c_void_p, c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32), c.c_void_p)
    k.WriteFile.argtypes = (c.c_void_p, c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32), c.c_void_p)
    k.CloseHandle.argtypes = (c.c_void_p,)
    k.DisconnectNamedPipe.argtypes = (c.c_void_p,)
    INVALID = c.c_void_p(-1).value


class FakeBridge(threading.Thread):
    """One-client pipe server; ``handler(line)`` returns the reply line, or None for silence."""

    def __init__(self, handler, name=None, delay=0.0):
        super().__init__(daemon=True)
        self.name_ = name or "pm_test_" + uuid.uuid4().hex[:8]
        self.handler, self.delay, self.seen = handler, delay, []
        self.ready = threading.Event()
        self.start()

    def run(self):
        time.sleep(self.delay)
        handle = k.CreateNamedPipeW("\\\\.\\pipe\\" + self.name_, 3, 0, 1, 65536, 65536, 0, None)
        assert handle not in (None, INVALID)
        self.ready.set()
        k.ConnectNamedPipe(handle, None)
        pending = b""
        while True:
            buffer, got = c.create_string_buffer(512), c.c_uint32()
            if not k.ReadFile(handle, buffer, 512, c.byref(got), None) or not got.value:
                break
            pending += buffer.raw[:got.value]
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                self.seen.append(line.decode())
                reply = self.handler(line.decode())
                if reply is not None:
                    data, wrote = (reply + "\n").encode(), c.c_uint32()
                    k.WriteFile(handle, data, len(data), c.byref(wrote), None)
        k.DisconnectNamedPipe(handle)
        k.CloseHandle(handle)


REPLIES = {
    "PING": "OK pong",
    "TIME": "OK 0.101",
    "PINS": "OK A,B,Q",
    "READ": "OK t=0.101 A=1 B=0 Q=Z",
    "SET Q 1": "OK",
    "EVENTS": "OK 0:A=Z;0.101:A=1;0.111:Q=0",
    "HOLD": "OK held t=0.101",
    "STEP 0.01": "OK held t=0.111",
    "RUN": "OK running",
    "APINS": "OK 1,2,3",
    "AREAD": "OK t=0.05 1=5 2=3.74999999 3=0",
}


@pytest.fixture
def bridge():
    server = FakeBridge(lambda line: REPLIES.get(line, "ERR unknown command"))
    server.ready.wait(5)
    client = BridgeClient(server.name_, timeout=3).connect(wait_seconds=3)
    yield client, server
    client.close()


def test_digital_commands(bridge):
    client, server = bridge
    assert client.ping() is True
    assert client.time() == pytest.approx(0.101)
    assert client.pins() == ["A", "B", "Q"]
    assert client.read() == dict(t=0.101, pins=dict(A="1", B="0", Q="Z"))
    client.set("Q", 1)
    assert client.events() == [dict(t=0.0, pin="A", value="Z"), dict(t=0.101, pin="A", value="1"),
                               dict(t=0.111, pin="Q", value="0")]
    assert client.hold() == pytest.approx(0.101)
    assert client.step(0.01) == pytest.approx(0.111)
    client.run()
    assert server.seen == ["PING", "TIME", "PINS", "READ", "SET Q 1", "EVENTS", "HOLD", "STEP 0.01", "RUN"]


def test_analog_commands(bridge):
    client, _ = bridge
    assert client.apins() == ["1", "2", "3"]
    reading = client.aread()
    assert reading["t"] == pytest.approx(0.05)
    assert reading["volts"] == dict(zip(["1", "2", "3"], [5.0, 3.74999999, 0.0]))


def test_err_reply_raises(bridge):
    client, _ = bridge
    with pytest.raises(BridgeError, match="unknown command"):
        client.request("BOGUS")


def test_empty_events_and_bad_arguments(bridge):
    client, server = bridge
    server.handler = lambda line: "OK " if line == "EVENTS" else "OK"
    assert client.events() == []
    with pytest.raises(ValueError):
        client.set("Q", 7)
    with pytest.raises(ValueError):
        client.step(0)


def test_timeout_raises_and_drops_connection():
    server = FakeBridge(lambda line: None)          # never answers
    server.ready.wait(5)
    client = BridgeClient(server.name_, timeout=0.3).connect(wait_seconds=3)
    with pytest.raises(BridgeError, match="no reply"):
        client.ping()
    assert not client.connected
    client.close()
    server.join(2)
    assert not server.is_alive(), "timed-out pipe must be closed, releasing its server"


def test_connect_failure_and_late_server():
    name = "pm_test_" + uuid.uuid4().hex[:8]
    with pytest.raises(BridgeError, match="cannot open bridge pipe"):
        BridgeClient(name).connect(wait_seconds=0)
    server = FakeBridge(lambda line: REPLIES.get(line, "ERR unknown command"), name=name, delay=0.6)
    with BridgeClient(name, timeout=5) as client:    # __enter__ waits for the pipe to appear
        assert client.ping()
    assert server.seen == ["PING"]


def test_not_connected():
    with pytest.raises(BridgeError, match="not connected"):
        BridgeClient("pm_test_nothing").ping()
