from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import can


DecodedCallback = Callable[[str, float, Dict[str, Any]], None]
RawCallback = Callable[[int, bytes, float, bool], None]


@dataclass
class PeriodicTask:
    msg_name: str
    period_s: float
    signal_values: Dict[str, Any]
    _stop: threading.Event
    _thread: threading.Thread


class CANBackend:
    """Thin wrapper around python-can to send and receive frames and decode via cantools.

    The decoder (cantools db) is provided by the caller via `encode`/`decode` functions.
    """

    def __init__(self) -> None:
        self._bus: Optional[can.BusABC] = None
        self._notifier: Optional[can.Notifier] = None
        self._rx_callback: Optional[DecodedCallback] = None
        self._raw_callback: Optional[RawCallback] = None
        self._periodic: Dict[str, PeriodicTask] = {}
        self._lock = threading.Lock()
        self._db_encode: Optional[Callable[[str, Dict[str, Any]], can.Message]] = None
        self._db_decode: Optional[Callable[[int, bytes], Dict[str, Any] | None]] = None
        self._id_to_name: Optional[Callable[[int], Optional[str]]] = None

    def set_db_interfaces(
        self,
        encode: Callable[[str, Dict[str, Any]], can.Message],
        decode: Callable[[int, bytes], Dict[str, Any] | None],
        id_to_name: Callable[[int], Optional[str]],
    ) -> None:
        self._db_encode = encode
        self._db_decode = decode
        self._id_to_name = id_to_name

    def connect(self, interface: str, channel: str, bitrate: Optional[int] = None) -> None:
        if self._bus is not None:
            self.disconnect()
        kwargs: Dict[str, Any] = {"interface": interface, "channel": channel}
        if interface == "socketcan":
            # Ensure we can also receive frames we transmit ourselves on socketcan
            kwargs["receive_own_messages"] = True
        if bitrate is not None and interface != "socketcan":
            # For socketcan on Linux, bitrate is configured at OS level; ignore here.
            kwargs["bitrate"] = bitrate
        self._bus = can.Bus(**kwargs)
        listener = _RxListener(self._on_raw_message)
        self._notifier = can.Notifier(self._bus, [listener], 0.01)

    def disconnect(self) -> None:
        with self._lock:
            for key in list(self._periodic.keys()):
                self.stop_periodic(key)
        if self._notifier:
            self._notifier.stop()
            self._notifier = None
        if self._bus:
            self._bus.shutdown()
            self._bus = None

    def on_decoded(self, callback: DecodedCallback) -> None:
        self._rx_callback = callback

    def on_raw(self, callback: RawCallback) -> None:
        self._raw_callback = callback

    def is_connected(self) -> bool:
        return self._bus is not None

    def send_once(self, msg_name: str, signal_values: Dict[str, Any]) -> None:
        assert self._bus, "Bus not connected"
        assert self._db_encode, "DB encode not set"
        msg = self._db_encode(msg_name, signal_values)
        self._bus.send(msg)

    def start_periodic(self, msg_name: str, signal_values: Dict[str, Any], period_ms: int) -> str:
        assert self._bus, "Bus not connected"
        key = f"{msg_name}:{int(time.time()*1000)}"
        stop_ev = threading.Event()
        th = threading.Thread(
            target=self._periodic_sender,
            args=(msg_name, signal_values, period_ms / 1000.0, stop_ev),
            daemon=True,
        )
        self._periodic[key] = PeriodicTask(
            msg_name=msg_name,
            period_s=period_ms / 1000.0,
            signal_values=signal_values.copy(),
            _stop=stop_ev,
            _thread=th,
        )
        th.start()
        return key

    def stop_periodic(self, key: str) -> None:
        task = self._periodic.pop(key, None)
        if task:
            task._stop.set()
            task._thread.join(timeout=1.0)

    # Internal
    def _periodic_sender(
        self, msg_name: str, signal_values: Dict[str, Any], period_s: float, stop_ev: threading.Event
    ) -> None:
        assert self._bus, "Bus not connected"
        assert self._db_encode, "DB encode not set"
        next_ts = time.time()
        while not stop_ev.is_set():
            msg = self._db_encode(msg_name, signal_values)
            try:
                self._bus.send(msg)
            except Exception:
                pass
            next_ts += period_s
            delay = max(0.0, next_ts - time.time())
            stop_ev.wait(delay)

    def _on_raw_message(self, msg: can.Message) -> None:
        # Always forward raw frames first
        rcb = self._raw_callback
        if rcb:
            try:
                rcb(msg.arbitration_id, bytes(msg.data), time.time(), bool(msg.is_extended_id))
            except Exception:
                pass

        if not (self._db_decode and self._id_to_name):
            return
        name = self._id_to_name(msg.arbitration_id)
        if name is None:
            return
        decoded = self._db_decode(msg.arbitration_id, msg.data)
        if decoded is None:
            return
        cb = self._rx_callback
        if cb:
            cb(name, time.time(), decoded)


class _RxListener(can.Listener):
    def __init__(self, cb: Callable[[can.Message], None]) -> None:
        super().__init__()
        self._cb = cb

    def on_message_received(self, msg: can.Message) -> None:  # type: ignore[override]
        self._cb(msg)
