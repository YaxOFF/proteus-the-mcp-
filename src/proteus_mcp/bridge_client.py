"""Python client for MCPBRIDGE.DLL, the probe that runs inside a Proteus simulation.

The DLL serves one named pipe per Proteus process (default ``proteus_mcp``). Every
request is one ASCII line and every reply is one line starting with ``OK`` or ``ERR``.

    with BridgeClient() as bridge:          # connects, waiting for the simulation to start
        bridge.hold()                       # freeze the digital simulation
        bridge.set("1", 1)                  # drive a digital probe pin
        bridge.step(0.01)                   # run exactly 10 ms of simulated time, then hold
        print(bridge.read())                # {'t': 0.111, 'pins': {'1': '1', ...}}
        print(bridge.aread())               # {'t': 0.1, 'volts': {'1': 5.0, ...}}
        bridge.run()                        # let the simulation run freely again

Digital probes (``PRIMITIVE=DIGITAL,MCPBRIDGE_D``) answer PINS/READ/SET/EVENTS/HOLD/STEP/RUN;
analog probes (``PRIMITIVE=ANALOG,MCPBRIDGE_A``) answer APINS/AREAD. See ``proteus_mcp.probe``
for placing a probe in a circuit.
"""
import re
import threading
import time

PIPE_PREFIX = "//./pipe/"


class BridgeError(Exception):
    """The bridge answered ``ERR ...`` or could not be reached."""


class BridgeClient:
    def __init__(self, pipe_name="proteus_mcp", timeout=10.0):
        self.pipe_name = pipe_name
        self.timeout = timeout
        self._pipe = None
        self._lock = threading.Lock()

    # ---- connection ---------------------------------------------------
    @property
    def path(self):
        name = self.pipe_name.replace("\\", "/")
        return name if name.startswith("//") else PIPE_PREFIX + name

    @property
    def connected(self):
        return self._pipe is not None

    def connect(self, wait_seconds=0.0):
        """Open the pipe, retrying for ``wait_seconds`` (the pipe exists only while a probe runs)."""
        if self._pipe is not None:
            return self
        deadline = time.monotonic() + wait_seconds
        last = None
        while True:
            try:
                self._pipe = open(self.path, "r+b", buffering=0)
                return self
            except OSError as error:
                last = error
            if time.monotonic() >= deadline:
                raise BridgeError(f"cannot open bridge pipe {self.path}: {last}. "
                                  "Is the simulation running with a probe attached?") from last
            time.sleep(0.2)

    def close(self):
        pipe, self._pipe = self._pipe, None
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass

    def __enter__(self):
        return self.connect(wait_seconds=self.timeout)

    def __exit__(self, *exc):
        self.close()

    # ---- raw protocol -------------------------------------------------
    def _readline(self):
        data = b""
        while not data.endswith(b"\n"):
            chunk = self._pipe.read(1)
            if not chunk:
                raise EOFError("bridge closed the pipe")
            data += chunk
        return data.decode("ascii", errors="replace").strip()

    def request(self, command, timeout=None):
        """Send one command line, return the text after ``OK``. Raises BridgeError on ``ERR``."""
        if self._pipe is None:
            raise BridgeError("not connected; call connect() first")
        timeout = self.timeout if timeout is None else timeout
        outcome = {}

        def exchange():
            try:
                self._pipe.write((command + "\n").encode("ascii"))
                outcome["reply"] = self._readline()
            except Exception as error:  # reported to the caller below
                outcome["error"] = error

        with self._lock:
            worker = threading.Thread(target=exchange, daemon=True)
            worker.start()
            worker.join(timeout)
            if worker.is_alive():
                # The pipe is now in an unknown state; drop it rather than mix up replies.
                self._pipe = None
                raise BridgeError(f"no reply to {command!r} within {timeout}s")
        if "error" in outcome:
            self.close()
            raise BridgeError(f"bridge connection lost during {command!r}: {outcome['error']}") from outcome["error"]
        reply = outcome["reply"]
        if reply.startswith("ERR"):
            raise BridgeError(reply[3:].strip() or "unknown error")
        if not reply.startswith("OK"):
            raise BridgeError(f"unexpected reply to {command!r}: {reply!r}")
        return reply[2:].strip()

    # ---- general ------------------------------------------------------
    def ping(self):
        return self.request("PING") == "pong"

    def time(self):
        """Current simulation time in seconds (digital time if a digital probe exists)."""
        return float(self.request("TIME"))

    # ---- digital probe ------------------------------------------------
    def pins(self):
        return _names(self.request("PINS"))

    def read(self):
        """``{'t': seconds, 'pins': {name: '0'|'1'|'Z'|'X'}}``"""
        t, values = _keyvalues(self.request("READ"))
        return dict(t=t, pins=values)

    def set(self, pin, value):
        """Drive a digital probe pin to 0, 1 or Z (release) from the next service point."""
        text = {0: "0", 1: "1", False: "0", True: "1", None: "Z"}.get(value, str(value).upper())
        if text not in ("0", "1", "Z"):
            raise ValueError("value must be 0, 1 or Z")
        if not re.fullmatch(r"\S+", str(pin)):
            raise ValueError("pin name must not contain spaces")
        self.request(f"SET {pin} {text}")

    def events(self):
        """Digital edges since the previous call: ``[{'t': s, 'pin': name, 'value': '0'|'1'|'Z'|'X'}]``."""
        out = []
        for item in filter(None, self.request("EVENTS").split(";")):
            match = re.fullmatch(r"([^:]+):(.+)=(.)", item)
            if match is None:
                raise BridgeError(f"bad EVENTS item: {item!r}")
            out.append(dict(t=float(match[1]), pin=match[2], value=match[3]))
        return out

    def hold(self):
        """Freeze the digital simulation; returns the simulated time it stopped at."""
        return _held_time(self.request("HOLD"))

    def step(self, seconds, timeout=300.0):
        """Run ``seconds`` of simulated time, then hold. Replies only when the target is reached."""
        if not seconds > 0:
            raise ValueError("seconds must be > 0")
        return _held_time(self.request(f"STEP {seconds:.12g}", timeout=timeout))

    def run(self):
        """Release a HOLD/STEP and let the simulation run freely."""
        self.request("RUN")

    # ---- analog probe -------------------------------------------------
    def apins(self):
        return _names(self.request("APINS"))

    def aread(self):
        """``{'t': seconds, 'volts': {name: volts}}`` from the latest accepted analog timepoint."""
        t, values = _keyvalues(self.request("AREAD"))
        return dict(t=t, volts={name: float(v) for name, v in values.items()})


def _names(text):
    return [name for name in text.split(",") if name]


def _keyvalues(text):
    """Parse ``t=<s> NAME=value ...`` into ``(t, {name: value})``."""
    t, values = None, {}
    for token in text.split():
        name, _, value = token.partition("=")
        if name == "t":
            t = float(value)
        else:
            values[name] = value
    if t is None:
        raise BridgeError(f"missing time in reply: {text!r}")
    return t, values


def _held_time(text):
    match = re.fullmatch(r"held t=(\S+)", text)
    if match is None:
        raise BridgeError(f"unexpected hold reply: {text!r}")
    return float(match[1])
