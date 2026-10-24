

def _hks_root():
    # dual-layout (round 62): <root> = 04_SOURCE (repo) or delivered-zip root;
    # HKS_AdminPanel/HKS_Client are siblings of it in BOTH layouts
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Reproduces the EXACT Acer failure from session_console.log:

    RuntimeError: '<Queue ... maxsize=600>' is bound to a different event loop

The old Connection created asyncio.Queues in __init__ (bound to whatever
loop first touched them). start_forever reconnects via asyncio.run() ->
a NEW event loop each time -> from the 2nd session on, _sender's
Queue.get() raised "bound to a different event loop" -> sender died ->
crash-loop -> desktop agent never ONLINE.

This test drives the REAL Connection._session through THREE sequential
asyncio.run() cycles (fresh event loop each, like real reconnects) and
requires that in EVERY session:
  1. a command from the server is executed, and
  2. an item enqueued on the out-queue is actually DRAINED AND SENT by
     the sender task (the thing that used to die on loop 2+).
"""
import asyncio
import json
import logging
import os
import sys

HERE = _hks_root()
ROOT = HERE  # <root> = 04_SOURCE (repo) or zip root
sys.path.insert(0, os.path.join(ROOT, "HKS_Client"))
os.environ.setdefault("HKS_DATA_DIR", os.path.join(HERE, "_queue_loop_tmp"))
os.makedirs(os.environ["HKS_DATA_DIR"], exist_ok=True)

from agent import connection as conn_mod  # noqa: E402
from agent.connection import Connection  # noqa: E402

PING_CALLS = []          # one entry per executed command
WIRE = []                # (session_index, raw_frames_sent)


class FakeServerWS:
    """Fake server connection: hello_ok, one cmd, then a 'blip'."""

    transport = None  # _pin_check tolerates no ssl object

    def __init__(self, session_idx):
        self.session_idx = session_idx
        self.sent = []
        self._greeted = False
        self._iter = 0

    async def recv(self):
        if not self._greeted:
            self._greeted = True
            return json.dumps({"type": "hello_ok", "machine_id": "Q"})
        raise ConnectionError("blip")  # ends hello path if called again

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._iter += 1
        if self._iter == 1:
            return json.dumps({"type": "cmd", "id": f"c{self.session_idx}",
                               "action": "queue_loop_ping", "params": {}})
        # grace so the executor handler runs and the sender drains the
        # queue before the "network blip" ends the session
        await asyncio.sleep(0.3)
        raise ConnectionError("blip")

    async def send(self, raw):
        self.sent.append(raw)
        WIRE.append((self.session_idx, raw))

    async def ping(self):
        return


class FakeConnectCM:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc):
        return False


def ping_handler(ctx, params):
    # simulate a worker thread pushing a reply through the out-queue
    # (exactly what real actions do for async progress / stream frames)
    PING_CALLS.append(params)
    ctx.conn.enqueue({"type": "note", " pong": len(PING_CALLS)})
    return {"ok": True, "data": {"pong": len(PING_CALLS)}}


class FakeCtx:
    role = "session"
    actions = {"queue_loop_ping": ping_handler}
    binary_handlers = {}
    stats = {"started": 0, "cmds": 0, "errors": 0, "latency_ms": None}
    offline = []
    log = logging.getLogger("fakectx")

    class _Cfg(dict):
        def save(self):
            pass
    cfg = _Cfg(server_url="ws://127.0.0.1:1/ws/agent", machine_id="Q",
               name="Q", token="t")

    def handle_disconnect(self):
        pass

    def send_error(self, *a):
        pass


def main():
    ctx = FakeCtx()
    conn = Connection(ctx, ctx.actions)
    real_connect = conn_mod.ws_connect
    wss = []

    def fake_connect(*a, **k):
        w = FakeServerWS(len(wss) + 1)
        wss.append(w)
        return FakeConnectCM(w)

    conn_mod.ws_connect = fake_connect
    try:
        for i in range(3):  # 3 reconnects = 3 fresh event loops
            try:
                asyncio.run(conn._session())
            except ConnectionError:
                pass  # expected "blip" ending each session
            except RuntimeError as e:
                if "different event loop" in str(e):
                    print(f"REPRODUCED the Acer bug at session {i + 1}: {e}")
                    sys.exit(1)
                raise
    finally:
        conn_mod.ws_connect = real_connect

    # every session executed its command...
    assert len(PING_CALLS) == 3, f"commands executed: {len(PING_CALLS)}, want 3"
    # ...and the sender drained the queue enqueued by the handler in
    # EVERY session (old code: sender dead from session 2 -> never sent)
    for idx in (1, 2, 3):
        frames = [raw for s, raw in WIRE if s == idx]
        assert any('"note"' in f for f in frames), \
            f"session {idx}: queue item never sent by sender (frames={frames})"
        assert any(f'"type": "result"' in f or '"type":"result"' in f
                   for f in frames), f"session {idx}: cmd result never sent"

    # inter-session enqueue from "worker threads" must be a silent no-op
    conn.enqueue({"type": "telemetry"})
    conn.enqueue_binary(b"\x04abc")

    print("  3 sequential asyncio.run loops on one Connection: no "
          "'bound to a different event loop'")
    print("  command executed + queue drained by sender in EVERY session")
    print("  inter-session enqueue: safe no-op")
    print("QUEUE-LOOP TESTS PASSED")


if __name__ == "__main__":
    main()
