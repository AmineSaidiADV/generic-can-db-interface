from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

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


# --- Helpers to bridge cantools with backend ---

def _make_db_interfaces(db: KCDDatabase):
    can_db = db.db

    def encode(msg_name: str, sig_vals: Dict[str, Any]):
        msg = can_db.get_message_by_name(msg_name)
        data = msg.encode(sig_vals)
        from can import Message

        return Message(arbitration_id=msg.frame_id, data=data, is_extended_id=msg.is_extended_frame)

    frame_id_map = {m.frame_id: m for m in can_db.messages}

    def id_to_name(frame_id: int) -> Optional[str]:
        m = frame_id_map.get(frame_id)
        return m.name if m else None

    def decode(frame_id: int, data: bytes) -> Optional[Dict[str, Any]]:
        try:
            m = frame_id_map.get(frame_id)
            if not m:
                return None
            return m.decode(data)
        except Exception:
            return None

    return encode, decode, id_to_name


# --- Sidebar: DB + Node + Bus config ---
with st.sidebar:
    st.header("Setup")
    kcd_path = st.text_input(
        "KCD path",
        value=str(Path(__file__).parent / "../CAN_Databases/Advantics_Generic_EVSE_protocol_v2.kcd"),
        help="Absolute or relative path to a .kcd file",
    )
    col_db = st.columns(2)
    if col_db[0].button("Load DB"):
        try:
            db = load_kcd(kcd_path)
            st.session_state.db = db
            st.session_state.node = None
            st.success(f"Loaded DB: {db.source_path.name}")
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

    def connect():
        if not st.session_state.db:
            st.warning("Load a KCD first")
            return
        be: CANBackend = st.session_state.backend
        enc, dec, id2name = _make_db_interfaces(st.session_state.db)
        be.set_db_interfaces(enc, dec, id2name)
        try:
            be.connect(interface=iface, channel=channel, bitrate=None if iface == "socketcan" else int(bitrate))
        except Exception as e:
            st.error(f"Failed to connect: {e}")
            return

        def on_decoded(msg_name: str, ts: float, signals: Dict[str, Any]):
            with st.session_state.lock:
                st.session_state.rx_last[msg_name] = {"time": ts, **signals}
                # Add to plot buffer with fully-qualified signal names
                for sname, val in signals.items():
                    fq = f"{msg_name}.{sname}"
                    if isinstance(val, (int, float)):
                        st.session_state.plot_buf.add(fq, ts, float(val))

        be.on_decoded(on_decoded)
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
                        if period_ms <= 0:
                            be.send_once(selected, values)
                            st.success("Sent once")
                        else:
                            key = be.start_periodic(selected, values, int(period_ms))
                            st.session_state.periodic[key] = {"msg": selected, "period_ms": int(period_ms), "values": values}
                            st.success(f"Started periodic: {key}")
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
