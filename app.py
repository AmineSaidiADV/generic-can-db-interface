from __future__ import annotations

import threading
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
from queue import Queue, Empty

import pandas as pd
import streamlit as st

from etka_generic_can_db_interface.can_backend import CANBackend
from etka_generic_can_db_interface.db_loader import KCDDatabase, load_kcd
from etka_generic_can_db_interface.plotting import TimeSeriesBuffer

st.set_page_config(page_title="ETKA Generic CAN DB", layout="wide")

# --- Session state init ---
if "db" not in st.session_state:
    st.session_state.db = None
if "node" not in st.session_state:
    st.session_state.node = None
if "backend" not in st.session_state:
    st.session_state.backend = CANBackend()
if "rx_table" not in st.session_state:
    st.session_state.rx_table = []
if "rx_last" not in st.session_state:
    st.session_state.rx_last = {}
if "plot_buf" not in st.session_state:
    st.session_state.plot_buf = TimeSeriesBuffer(maxlen=5000)
if "plot_signals" not in st.session_state:
    st.session_state.plot_signals = []
if "periodic" not in st.session_state:
    st.session_state.periodic = {}
if "connected" not in st.session_state:
    st.session_state.connected = False
if "lock" not in st.session_state:
    st.session_state.lock = threading.Lock()

# Thread-safe queue to receive decoded frames from CAN background thread
_rx_queue: "Queue[tuple[str, float, Dict[str, Any]]]" = Queue(maxsize=10000)
_raw_queue: "Queue[tuple[int, bytes, float, bool]]" = Queue(maxsize=10000)

def _drain_rx_queue() -> int:
    """Move decoded frames from the background queue into session state.

    Returns number of messages drained.
    """
    count = 0
    try:
        while True:
            name, ts, signals = _rx_queue.get_nowait()
            # Update last-seen table
            st.session_state.rx_last[name] = {"time": ts, **signals}
            # Add to plot buffer
            for sname, val in signals.items():
                if isinstance(val, (int, float)):
                    st.session_state.plot_buf.add(f"{name}.{sname}", ts, float(val))
            count += 1
    except Empty:
        pass
    return count

def _drain_raw_queue(max_keep: int = 500) -> int:
    count = 0
    raw_list = st.session_state.get("rx_raw", [])
    try:
        while True:
            arb_id, data, ts, is_ext = _raw_queue.get_nowait()
            raw_list.append(
                {
                    "time": ts,
                    "can_id": f"0x{arb_id:X}",
                    "extended": is_ext,
                    "dlc": len(data),
                    "data": data.hex(" "),
                }
            )
            count += 1
    except Empty:
        pass
    # Keep bounded size
    if len(raw_list) > max_keep:
        raw_list = raw_list[-max_keep:]
    st.session_state.rx_raw = raw_list
    return count


# --- Helpers to bridge cantools with backend ---

def _make_db_interfaces(db: KCDDatabase):
    can_db = db.db

    def encode(msg_name: str, sig_vals: Dict[str, Any]):
        msg = can_db.get_message_by_name(msg_name)
        data = msg.encode(sig_vals)
        from can import Message

        return Message(arbitration_id=msg.frame_id, data=data, is_extended_id=msg.is_extended_frame)

    def id_to_name(frame_id: int) -> Optional[str]:
        try:
            m = can_db.get_message_by_frame_id(frame_id)
            return m.name
        except Exception:
            return None

    def decode(frame_id: int, data: bytes) -> Optional[Dict[str, Any]]:
        try:
            return can_db.decode_message(frame_id, data)
        except Exception:
            return None

    return encode, decode, id_to_name


# --- Sidebar: DB + Node + Bus config ---
with st.sidebar:
    st.header("Setup")
    kcd_path = st.text_input(
        "KCD path",
        value=st.session_state.get("kcd_path", ""),
        key="kcd_path",
        help="Absolute or relative path to a .kcd file",
    )
    col_db = st.columns(2)
    if col_db[0].button("Load DB"):
        try:
            db = load_kcd(kcd_path)
            st.session_state.db = db
            st.session_state.node = None
            st.success(f"Loaded DB: {db.source_path.name}")
            # If already connected, refresh backend enc/dec mapping to this DB
            if st.session_state.connected:
                try:
                    be: CANBackend = st.session_state.backend
                    enc, dec, id2name = _make_db_interfaces(st.session_state.db)
                    be.set_db_interfaces(enc, dec, id2name)
                    st.info("Updated CAN backend to use the newly loaded database.")
                except Exception as e:
                    st.warning(f"Connected, but failed updating backend to new DB: {e}")
        except Exception as e:
            st.error(f"Failed to load DB: {e}")

    if st.session_state.db:
        node = st.selectbox("Our node (producer)", options=["<select>"] + st.session_state.db.nodes)
        st.session_state.node = None if node == "<select>" else node

    st.divider()
    st.subheader("CAN Bus")
    iface = st.selectbox("Interface", ["socketcan", "pcan", "kvaser"], index=0)
    channel = st.text_input("Channel", value="vcan0")
    bitrate = st.number_input("Bitrate (non-socketcan)", min_value=1000, max_value=1000000, value=500000, step=1000)
    use_filters = st.checkbox("Apply CAN filters (only DB messages)", value=True, 
                              help="When enabled, only receive CAN IDs defined in the loaded database. Disable to receive all CAN traffic.")

    def connect():
        if not st.session_state.db:
            st.warning("Load a KCD first")
            return
        be: CANBackend = st.session_state.backend
        enc, dec, id2name = _make_db_interfaces(st.session_state.db)
        be.set_db_interfaces(enc, dec, id2name)
        try:
            # Build hardware filters: allow all messages defined in the DB
            # (regardless of whether they have declared producers/senders)
            filters = None
            if use_filters:
                filters = []
                try:
                    for m in st.session_state.db.db.messages:  # type: ignore
                        mask = 0x1FFFFFFF if m.is_extended_frame else 0x7FF
                        filters.append({
                            "can_id": int(m.frame_id),
                            "can_mask": int(mask),
                            "extended": bool(m.is_extended_frame),
                        })
                except Exception:
                    filters = None  # type: ignore

            be.connect(
                interface=iface,
                channel=channel,
                bitrate=None if iface == "socketcan" else int(bitrate),
                can_filters=filters,  # type: ignore[arg-type]
            )
        except Exception as e:
            st.error(f"Failed to connect: {e}")
            return

        def on_decoded(msg_name: str, ts: float, signals: Dict[str, Any]):
            # From background thread: do not touch Streamlit session_state directly
            try:
                _rx_queue.put_nowait((msg_name, ts, signals))
            except Exception:
                # Drop if queue is full; better to miss data than block thread
                pass

        be.on_decoded(on_decoded)
        # Raw frames handler
        def on_raw(arb_id: int, data: bytes, ts: float, is_ext: bool):
            try:
                _raw_queue.put_nowait((arb_id, data, ts, is_ext))
            except Exception:
                pass
        be.on_raw(on_raw)
        st.session_state.connected = True

    def disconnect():
        st.session_state.backend.disconnect()
        st.session_state.connected = False

    c1, c2 = st.columns(2)
    if not st.session_state.connected:
        if c1.button("Connect"):
            connect()
    else:
        if c2.button("Disconnect"):
            disconnect()


# --- Main: Tabs ---
if not st.session_state.db:
    st.info("Load a KCD database from the left sidebar to begin.")
    st.stop()

producer_tab, monitor_tab = st.tabs(["Producer: Send", "Monitor & Plot"])

with producer_tab:
    st.subheader("Send producer messages")
    if not st.session_state.node:
        st.warning("Select your node in the sidebar to see producer messages.")
    else:
        db = st.session_state.db
        prod_msgs = db.producer_messages_for(st.session_state.node)
        if not prod_msgs:
            st.info("No messages where this node is the sender.")
        else:
            names = [m.name for m in prod_msgs]
            selected = st.selectbox("Message", names)
            msg_def = db.get_message(selected)
            with st.form("send_form"):
                cols = st.columns(2)
                values: Dict[str, Any] = {}
                for i, sig in enumerate(msg_def.signals):
                    col = cols[i % 2]
                    default = 0.0
                    step = 1.0
                    if sig.scale is not None:
                        step = max(1.0, abs(sig.scale))
                    minv = sig.minimum if sig.minimum is not None else 0.0
                    maxv = sig.maximum if sig.maximum is not None else float(minv + 1000)
                    v = col.number_input(
                        f"{sig.name}", value=float(default), step=float(step), min_value=float(minv), max_value=float(maxv)
                    )
                    values[sig.name] = v
                c3, c4, c5 = st.columns([1, 1, 2])
                period_ms = c3.number_input("Period (ms)", min_value=0, max_value=60000, value=0, step=10)
                submit = c4.form_submit_button("Send")
                if submit:
                    if not st.session_state.connected:
                        st.warning("Connect to a CAN interface first.")
                    else:
                        be: CANBackend = st.session_state.backend
                        try:
                            if period_ms <= 0:
                                be.send_once(selected, values)
                                st.success("Sent once")
                            else:
                                key = be.start_periodic(selected, values, int(period_ms))
                                st.session_state.periodic[key] = {
                                    "msg": selected,
                                    "period_ms": int(period_ms),
                                    "values": values,
                                }
                                st.success(f"Started periodic: {key}")
                        except Exception as e:
                            st.error(f"Failed to send '{selected}': {e}")
            if st.session_state.periodic:
                st.write("Active periodic sends:")
                for key, info in list(st.session_state.periodic.items()):
                    colA, colB, colC, colD = st.columns([2, 2, 4, 2])
                    colA.write(info["msg"])  # type: ignore
                    colB.write(f"{info['period_ms']} ms")  # type: ignore
                    colC.write(", ".join(f"{k}={v}" for k, v in info["values"].items()))  # type: ignore
                    if colD.button("Stop", key=f"stop_{key}"):
                        st.session_state.backend.stop_periodic(key)
                        st.session_state.periodic.pop(key, None)

with monitor_tab:
    st.subheader("Monitor decoded messages")
    # Pull any newly decoded frames into session state
    dec = _drain_rx_queue()
    raw = _drain_raw_queue()
    cxa, cxb, cxc = st.columns(3)
    cxa.metric("Decoded frames (this refresh)", dec)
    cxb.metric("Raw frames (this refresh)", raw)
    hide_producer = st.checkbox("Hide producer messages", value=True)
    rx_last = st.session_state.rx_last
    if rx_last:
        rows: List[Dict[str, Any]] = []
        for name, payload in rx_last.items():
            if hide_producer and st.session_state.node:
                # hide if selected node is a sender of this message
                try:
                    msg_def = st.session_state.db.db.get_message_by_name(name)  # type: ignore
                    if st.session_state.node in (msg_def.senders or []):
                        continue
                except Exception:
                    pass
            row = {"message": name, **payload}
            rows.append(row)
        if rows:
            df = pd.DataFrame(rows).sort_values("time", ascending=False)
            st.dataframe(df, use_container_width=True, height=300)
        else:
            st.info("No messages to display with current filters.")
    else:
        st.info("No messages received yet.")

    st.divider()
    st.subheader("Plot signals over time")
    # Build selectable list of all signals known from db
    all_signal_fqn: List[str] = []
    for m in st.session_state.db.db.messages:  # type: ignore
        for s in m.signals:
            all_signal_fqn.append(f"{m.name}.{s.name}")
    selected = st.multiselect("Signals to plot", options=sorted(all_signal_fqn), default=st.session_state.plot_signals)
    st.session_state.plot_signals = selected
    df = st.session_state.plot_buf.as_dataframe(selected)
    if df.empty:
        st.info("No data yet for selected signals.")
    else:
        st.line_chart(df, use_container_width=True, height=300)

    with st.expander("Raw frames (last 500)"):
        raw_rows = st.session_state.get("rx_raw", [])
        if raw_rows:
            rdf = pd.DataFrame(raw_rows).sort_values("time", ascending=False)
            st.dataframe(rdf, use_container_width=True, height=240)
        else:
            st.caption("No raw frames captured yet.")

# Optional auto-refresh to update monitor without manual interaction
with st.sidebar:
    if st.session_state.connected:
        st.divider()
        st.subheader("Refresh")
        auto_refresh = st.checkbox("Auto-refresh monitor", value=True, key="auto_refresh")
        interval_ms = st.number_input("Interval (ms)", min_value=200, max_value=5000, value=1000, step=100)
        if auto_refresh:
            # Sleep briefly and rerun to pull latest RX data added by background thread
            time.sleep(float(interval_ms) / 1000.0)
            # Streamlit modern API uses st.rerun(); experimental_rerun was removed in some versions
            st.rerun()
