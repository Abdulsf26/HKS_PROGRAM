"""HKS Agent - outbound WebSocket connection with auto-reconnect, TOFU cert
pinning, keepalive, and background sender queue."""
import asyncio
import hashlib
import json
import ssl
import time
import traceback

try:  # websockets >= 13 (new asyncio client)
    from websockets.asyncio.client import connect as ws_connect
    WS_NEW = True
except ImportError:  # older versions
    from websockets.client import connect as ws_connect  # type: ignore
    WS_NEW = False

MAX_FRAME = 16 * 1024 * 1024


def _command_envelope(result):
    """Keep handler payload fields visible through the websocket/API envelope.

    Handlers returning {ok: true, note: ...} were previously sent with data=null;
    the panel unwrapped only data and showed a misleading generic 'action ok'.
    """
    if not isinstance(result, dict):
        return {"ok": True, "data": {} if result is None else result}
    if "ok" not in result:
        return {"ok": True, "data": result}
    if "data" not in result:
        payload = {k: v for k, v in result.items() if k not in ("ok", "error")}
        return {**result, "data": payload or None}
    return result


def _ssl_context(cfg):
    if not cfg.get("server_url", "").startswith("wss"):
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # TOFU: trust on first use, then pin the certificate fingerprint.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _pin_check(transport, cfg):
    """After connecting over TLS, verify (or learn) the server certificate pin."""
    try:
        peercert = transport.get_extra_info("ssl_object").getpeercert(binary_form=True)
        if not peercert:
            return True
        pin = hashlib.sha256(peercert).hexdigest()
        saved = cfg.get("cert_pin", "")
        if not saved:
            cfg["cert_pin"] = pin
            cfg.save()
            return True
        if saved != pin:
            raise PermissionError("server certificate changed (possible MITM). "
                                  f"expected pin {saved[:16]}… got {pin[:16]}… "
                                  "(if you just REINSTALLED HKS, run "
                                  "INSTALL_HKS.bat once - 7.2.38+ resets the "
                                  "pin and the agent reconnects)")
        return True
    except PermissionError:
        raise
    except Exception:
        # R05: fail CLOSED when a pin is configured - an unexpected error
        # must never become "trusted". Only pin-less (LAN, plain ws) runs
        # tolerate the soft path.
        if cfg.get("cert_pin"):
            raise
        return True  # plain ws or no ssl object


class Connection:
    """Manages the single outbound link. Reconnects forever with backoff."""

    def __init__(self, ctx, actions, on_connect=None):
        self.ctx = ctx
        self.actions = actions
        self.on_connect = on_connect
        self.alive = False
        self.ws = None
        # NOTE: queues MUST be created inside the running event loop (in
        # _session), not here. Created in __init__ they bind to whichever
        # loop first touches them; every reconnect runs a NEW loop
        # (asyncio.run), so the second session onwards crashed with
        # "Queue is bound to a different event loop" -> permanent OFFLINE.
        self._outq = None
        self._binq = None
        self._loop = None
        self.backoff = 3
        self._command_tasks = set()
        self.started_at = time.time()
        self.last_connected_at = None
        self.last_error = ""
        self.connection_stage = "starting"
        self.connection_attempts = 0

    # ---------- thread-safe enqueue (called from worker threads) ----------
    def enqueue(self, obj):
        if self._loop is None or self._outq is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._put, self._outq, json.dumps(obj, default=str))
        except Exception:
            pass

    def enqueue_binary(self, data: bytes):
        if self._loop is None or self._binq is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._put, self._binq, data)
        except Exception:
            pass

    @staticmethod
    def _put(q, item):
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                q.get_nowait()  # drop oldest (keeps live streams fresh)
                q.put_nowait(item)
            except Exception:
                pass

    # ---------- main loop ----------
    def start_forever(self):
        while True:
            from .common import kill_switch_active
            if kill_switch_active():
                self.ctx.log.warning("SELF_DESTRUCT marker present — agent exiting")
                return
            self.connection_attempts += 1
            self.connection_stage = "connecting"
            try:
                asyncio.run(self._session())
                self.last_error = "WebSocket I/O ended; reconnecting"
                self.connection_stage = "reconnecting"
                self.ctx.log.warning("WebSocket closed cleanly; reconnecting")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                self.alive = False
                self.last_error = f"{self.connection_stage}: {str(e)[:300]}"
                self.ctx.log.error("connection %s failed: %s", self.connection_stage, e)
                self.connection_stage = "reconnecting"
            import random
            self.connection_stage = "reconnect_wait"
            time.sleep(self.backoff + random.uniform(0, self.backoff * 0.1))  # jitter (audit4)
            self.backoff = min(self.backoff * 2, 30)  # 30 s cap: back FAST after sleep/wake

    async def _session(self):
        cfg = self.ctx.cfg
        # fresh queues for THIS loop (see __init__ note — fixes the
        # "bound to a different event loop" reconnect crash)
        self._outq = asyncio.Queue(maxsize=600)
        self._binq = asyncio.Queue(maxsize=600)
        header_kw = "additional_headers" if WS_NEW else "extra_headers"
        ws = None
        try:
          async with ws_connect(cfg["server_url"], max_size=MAX_FRAME,
                              ssl=_ssl_context(cfg),
                              open_timeout=15,
                              ping_interval=20, ping_timeout=25,
                              **{header_kw: {}}) as ws:
            self.connection_stage = "tls_check"
            _pin_check(ws.transport, cfg)
            self.ws = ws
            self._loop = asyncio.get_running_loop()
            hello = {"type": "hello", "machine_id": cfg["machine_id"], "role": self.ctx.role,
                     "name": cfg.get("name", "laptop"), "version": "1.0.0",
                     "token": cfg["token"], "caps": sorted(self.actions.keys())}
            await ws.send(json.dumps(hello))
            self.connection_stage = "handshake"
            rep = json.loads(await asyncio.wait_for(ws.recv(), 15))
            if rep.get("type") != "hello_ok":
                raise PermissionError(f"server rejected agent: {rep}")
            self.alive = True
            self.backoff = 3
            self.last_connected_at = time.time()
            self.last_error = ""
            self.connection_stage = "connected"
            self.ctx.log.info("connected to server as role=%s", self.ctx.role)
            self.ctx.conn = self
            latency_task = asyncio.create_task(self._latency_loop(ws), name="hks-latency")
            # flush offline queue
            while self.ctx.offline:
                try:
                    await ws.send(json.dumps(self.ctx.offline.pop(0), default=str))
                except Exception:
                    break
            workers = [
                asyncio.create_task(self._sender(ws), name="hks-sender"),
                asyncio.create_task(self._receiver(ws), name="hks-receiver"),
                asyncio.create_task(self._telemetry_loop(), name="hks-telemetry"),
            ]
            self.connection_stage = "socket_io"
            try:
                # Receiver/sender completion means this websocket session is over.
                # gather() hangs forever if recv completes cleanly while send waits
                # on an empty queue (the common server-close/offline failure).
                done, _ = await asyncio.wait(workers[:2], return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc:
                        raise exc
                raise ConnectionError("WebSocket I/O task returned normally")
            finally:
                self.alive = False
                for task in workers + [latency_task]:
                    if not task.done():
                        task.cancel()
                pending_commands = list(self._command_tasks)
                for task in pending_commands:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*workers, latency_task, *pending_commands,
                                     return_exceptions=True)
                if self.ws is ws:
                    self.ws = None
                if self._loop is asyncio.get_running_loop():
                    self._loop = None
                self._outq = self._binq = None
                self._command_tasks.clear()
                self.ctx.handle_disconnect()  # stop screen/cam/mic/talk streams
        except BaseException:
            self.alive = False
            if self.ws is ws:
                self.ws = None
            if self._loop is asyncio.get_running_loop():
                self._loop = None
            self._outq = self._binq = None
            raise

    async def _sender(self, ws):
        while True:
            # interleave json + binary, biased to json (control plane first)
            get_json = self._outq.get()
            get_bin = self._binq.get()
            json_task = asyncio.ensure_future(get_json)
            bin_task = asyncio.ensure_future(get_bin)
            done, pending = await asyncio.wait({json_task, bin_task},
                                               return_when=asyncio.FIRST_COMPLETED)
            payloads = []
            for t in done:
                try:
                    payloads.append(t.result())
                except Exception:
                    pass
            # Resolve the OTHER side BEFORE the first await (ws.send): a task
            # that finished during the wait must have its dequeued message
            # SENT, not cancelled; a still-waiting task is safe to cancel
            # (queue.get() gives the item back). Field audit 2026-09-23.
            for t in pending:
                if t.done():
                    try:
                        payloads.append(t.result())
                    except Exception:
                        pass
                else:
                    t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for p in payloads:
                await ws.send(p)

    async def _receiver(self, ws):
        loop = asyncio.get_running_loop()
        pool = asyncio.Semaphore(3)  # max concurrent handlers (resource guard)
        async for raw in ws:
            if isinstance(raw, (bytes, bytearray)):
                # binary frame: first byte = channel (4 = admin voice)
                if not raw:
                    continue
                h = self.ctx.binary_handlers.get(raw[0])
                if h:
                    try:
                        h(bytes(raw[1:]))
                    except Exception as e:
                        self.ctx.send_error("binary", f"channel {raw[0]}: {e}",
                                            traceback.format_exc())
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get("type")
            if t == "cmd":
                # slot taken inside the task now - a loop teardown before the
                # task runs can no longer leak it (audit2 #13)
                task = asyncio.create_task(self._run_cmd(loop, pool, msg, ws),
                                           name="hks-command")
                self._command_tasks.add(task)
                task.add_done_callback(self._command_tasks.discard)
            elif t == "config":
                self._apply_config(msg)

    def _apply_config(self, msg):
        cfg = self.ctx.cfg
        for k in ("fps", "quality", "max_width", "telemetry_sec"):
            if k in msg:
                try:
                    cfg[k] = msg[k]
                except Exception:
                    pass
        cfg.save()
        self.ctx.log.info("config updated from server: %s", msg)

    async def _run_cmd(self, loop, pool, msg, ws):
        await pool.acquire()  # audit2 #13: acquire/release fully inside
        try:
            await self._run_cmd_inner(loop, msg, ws)
        finally:
            pool.release()

    async def _run_cmd_inner(self, loop, msg, ws=None):
        cid, action = msg.get("id"), msg.get("action", "")
        params = msg.get("params", {}) or {}
        handler = self.actions.get(action)
        if handler is None:
            res = {"ok": False, "error": f"unknown action: {action}"}
        else:
            def work():
                try:
                    return handler(self.ctx, params)
                except Exception as e:
                    self.ctx.send_error(action, str(e), traceback.format_exc())
                    return {"ok": False, "error": str(e)}
            try:
                res = await loop.run_in_executor(None, work)
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            # Preserve action details through the relay: add-on launch PID/note,
            # file operation counts and other successful payloads must reach UI.
            res = _command_envelope(res)
            self.ctx.stats["cmds"] += 1
        try:
            if ws is not None:
                await ws.send(json.dumps({"type": "result", "id": cid, **res, "data": res.get("data")}))
        except Exception:
            pass
        # (single release lives in the _run_cmd wrapper - audit2 #13)

    async def _telemetry_loop(self):
        """Periodic battery / network / cpu snapshot (only svc role sends)."""
        if self.ctx.role != "svc":
            return
        from .modules import sysinfo as telemetry_mod
        while self.alive:
            try:
                snap = telemetry_mod.snapshot()
                if snap:
                    self.ctx.send_telemetry(snap)
            except Exception as e:
                self.ctx.log.error("telemetry: %s", e)
            await asyncio.sleep(max(2, int(self.ctx.cfg.get("telemetry_sec", 5))))

    async def _latency_loop(self, ws):
        """Protocol-level ping every 20s; records round-trip latency in stats.

        (Was referenced but missing after the v2 refactor — the agent connected
        and then crashed instantly, showing permanently OFFLINE in the panel.)
        """
        try:
            while True:
                await asyncio.sleep(20)
                try:
                    t0 = time.monotonic()
                    await asyncio.wait_for(ws.ping(), timeout=10)
                    self.ctx.stats["latency_ms"] = round((time.monotonic() - t0) * 1000)
                except Exception:
                    pass  # transient; a dead socket is caught by the receiver
        except asyncio.CancelledError:
            raise
