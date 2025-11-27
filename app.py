from __future__ import annotations

import threading
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
from queue import Queue, Empty
from datetime import datetime

import pandas as pd
import streamlit as st

from etka_generic_can_db_interface.can_backend import CANBackend
from etka_generic_can_db_interface.db_loader import KCDDatabase, load_kcd
from etka_generic_can_db_interface.plotting import TimeSeriesBuffer, build_signal_plot

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
    st.session_state.plot_buf = TimeSeriesBuffer(maxlen=1000)  # Reduced from 5000
if "plot_signals" not in st.session_state:
    st.session_state.plot_signals = []
if "periodic" not in st.session_state:
    st.session_state.periodic = {}
if "connected" not in st.session_state:
    st.session_state.connected = False
if "lock" not in st.session_state:
    st.session_state.lock = threading.Lock()
if "refresh_interval" not in st.session_state:
    st.session_state.refresh_interval = 1000
if "rx_raw" not in st.session_state:
    st.session_state.rx_raw = []
if "rx_prev" not in st.session_state:
    # Track previous values to detect changes
    st.session_state.rx_prev = {}
if "msg_order" not in st.session_state:
    st.session_state.msg_order = {}
if "msg_order_counter" not in st.session_state:
    st.session_state.msg_order_counter = 0

# Thread-safe queue to receive decoded frames from CAN background thread
# CRITICAL: Store in session_state so they persist across Streamlit reruns!
# Otherwise, each rerun creates NEW queues while callbacks reference OLD ones.
if "rx_queue" not in st.session_state:
    st.session_state.rx_queue = Queue(maxsize=1000)  # Reduced from 10000
if "raw_queue" not in st.session_state:
    st.session_state.raw_queue = Queue(maxsize=1000)  # Reduced from 10000

# Convenience references (but callbacks MUST use st.session_state.rx_queue)
_rx_queue = st.session_state.rx_queue
_raw_queue = st.session_state.raw_queue


def _format_signal_value(value: Any) -> str:
    """Return a concise string representation for display tables."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return ("{:.6g}".format(value)).rstrip("0").rstrip(".") or "0"
    return str(value)

def _drain_rx_queue() -> int:
    """Move decoded frames from the background queue into session state.

    Returns number of messages drained.
    """
    count = 0
    try:
        while True:
            name, ts, signals = _rx_queue.get_nowait()
            # Track previous values for change detection
            prev_signals = st.session_state.rx_prev.get(name, {})
            st.session_state.rx_prev[name] = signals.copy()

            # Remember first-seen order to keep UI stable
            if name not in st.session_state.msg_order:
                st.session_state.msg_order[name] = st.session_state.msg_order_counter
                st.session_state.msg_order_counter += 1
            
            # Update last-seen table (only stores latest value per message)
            st.session_state.rx_last[name] = {"time": ts, "prev": prev_signals, **signals}
            # Add to plot buffer only for numeric signals
            for sname, val in signals.items():
                if isinstance(val, (int, float)):
                    st.session_state.plot_buf.add(f"{name}.{sname}", ts, float(val))
            count += 1
    except Empty:
        pass
    
    # Periodically cleanup unused signals from plot buffer (every 100 messages)
    if count > 0 and len(st.session_state.plot_signals) > 0:
        # Build list of all possible signals we might want to keep
        keep_signals = set(st.session_state.plot_signals)
        # Also keep signals from currently visible messages
        for msg_name in st.session_state.rx_last.keys():
            if st.session_state.db:
                try:
                    msg = st.session_state.db.db.get_message_by_name(msg_name)
                    for sig in msg.signals:
                        keep_signals.add(f"{msg_name}.{sig.name}")
                except Exception:
                    pass
        st.session_state.plot_buf.cleanup_unused_signals(list(keep_signals))
    
    return count

def _drain_raw_queue(max_keep: int = 300) -> int:  # Reduced from 500
    count = 0
    raw_list = st.session_state.get("rx_raw", [])
    try:
        while True:
            arb_id, data, ts, is_ext = _raw_queue.get_nowait()
            raw_list.append(
                {
                    "timestamp": ts,  # Keep original for sorting
                    "time": datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3],
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
            st.session_state.rx_last = {}
            st.session_state.rx_prev = {}
            st.session_state.msg_order = {}
            st.session_state.msg_order_counter = 0
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
    channel = st.text_input("Channel", value="can0")
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

        # Capture queue references in local scope BEFORE creating callbacks
        # (background threads can't access st.session_state directly)
        rx_queue = st.session_state.rx_queue
        raw_queue = st.session_state.raw_queue

        def on_decoded(msg_name: str, ts: float, signals: Dict[str, Any]):
            # From background thread: use captured queue reference (not st.session_state)
            try:
                rx_queue.put_nowait((msg_name, ts, signals))
            except Exception as e:
                # Drop if queue is full; better to miss data than block thread
                print(f"[DEBUG] Failed to add to rx_queue: {e}")

        be.on_decoded(on_decoded)

        # Raw frames handler
        def on_raw(arb_id: int, data: bytes, ts: float, is_ext: bool):
            try:
                raw_queue.put_nowait((arb_id, data, ts, is_ext))
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
    
    # Show connection status
    if st.session_state.connected:
        st.success(f"✅ Connected to {channel}")
        st.caption(f"Filters: {'Enabled' if use_filters else 'Disabled (all traffic)'}")
        st.divider()
        st.subheader("Auto-Refresh")
        auto_refresh = st.checkbox("Auto-refresh monitor", value=True, key="auto_refresh", 
                                    help="Automatically refresh to show new CAN messages")
        interval_ms = st.number_input("Interval (ms)", min_value=200, max_value=5000, value=1000, step=100,
                                      help="How often to refresh the display")
        st.session_state.refresh_interval = interval_ms
    else:
        st.info("Not connected")


# --- Main: Tabs ---
if not st.session_state.db:
    st.info("Load a KCD database from the left sidebar to begin.")
    st.stop()

# Drain queues on every refresh (before rendering tabs)
if st.session_state.connected:
    _drain_rx_queue()
    _drain_raw_queue()

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
                
                # Check for stop requests from previous run
                if "stop_periodic_key" in st.session_state:
                    key_to_stop = st.session_state.stop_periodic_key
                    try:
                        st.session_state.backend.stop_periodic(key_to_stop)
                        st.session_state.periodic.pop(key_to_stop, None)
                        st.success(f"Stopped periodic task: {key_to_stop}")
                    except Exception as e:
                        st.error(f"Error stopping task: {e}")
                    finally:
                        del st.session_state.stop_periodic_key
                
                for key, info in list(st.session_state.periodic.items()):
                    colA, colB, colC, colD = st.columns([2, 2, 4, 2])
                    colA.write(info["msg"])  # type: ignore
                    colB.write(f"{info['period_ms']} ms")  # type: ignore
                    colC.write(", ".join(f"{k}={v}" for k, v in info["values"].items()))  # type: ignore
                    
                    if colD.button("Stop", key=f"stop_{key}"):
                        st.session_state.stop_periodic_key = key
                        st.rerun()

with monitor_tab:
    st.subheader("Monitor decoded messages")
    
    # Show queue sizes and data counts
    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("Active messages", len(st.session_state.rx_last))
    col_m2.metric("Queue size", _rx_queue.qsize())
    
    hide_producer = st.checkbox("Hide producer messages", value=True)
    
    rx_last = st.session_state.rx_last
    if not rx_last:
        st.info("No messages received yet.")
    else:
        # Filter messages
        messages_to_show = []
        for name, payload in rx_last.items():
            if hide_producer and st.session_state.node:
                # hide if selected node is a sender of this message
                try:
                    msg_def = st.session_state.db.db.get_message_by_name(name)  # type: ignore
                    if st.session_state.node in (msg_def.senders or []):
                        continue
                except Exception:
                    pass
            messages_to_show.append((name, payload))
        
        if not messages_to_show:
            st.info("No messages to display with current filters.")
        else:
            # Sort by first-seen order to keep layout stable
            messages_to_show.sort(key=lambda x: st.session_state.msg_order.get(x[0], float("inf")))

            for name, payload in messages_to_show:
                ts = payload.get("time", 0.0)
                prev_signals = payload.get("prev", {})

                msg_def = None
                signal_names: List[str] = []
                try:
                    msg_def = st.session_state.db.db.get_message_by_name(name)  # type: ignore
                    signal_names = [sig.name for sig in msg_def.signals]
                except Exception:
                    pass

                if not signal_names:
                    signal_names = [k for k in payload.keys() if k not in ("time", "prev")]

                table_rows: List[Dict[str, str]] = []
                changed_preview: List[str] = []
                steady_preview: List[str] = []
                changed_count = 0

                for sig_name in signal_names:
                    current_val = payload.get(sig_name)
                    prev_val = prev_signals.get(sig_name)
                    formatted_current = _format_signal_value(current_val)
                    formatted_prev = _format_signal_value(prev_val)
                    changed = prev_val is not None and current_val != prev_val

                    if changed:
                        changed_count += 1
                        if len(changed_preview) < 3:
                            changed_preview.append(f"{sig_name}={formatted_current}")
                    elif current_val is not None and len(steady_preview) < 3:
                        steady_preview.append(f"{sig_name}={formatted_current}")

                    table_rows.append(
                        {
                            "Signal": sig_name,
                            "Current": formatted_current,
                            "Previous": formatted_prev,
                            "Changed": "Yes" if changed else "",
                        }
                    )

                if not table_rows:
                    continue

                time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
                preview = changed_preview or steady_preview
                summary_preview = ", ".join(preview)
                header = f"{name} · {time_str} · {changed_count}/{len(table_rows)} changed"
                if summary_preview:
                    header = f"{header} · {summary_preview}"

                toggle_key = f"msg_open_{name}"
                button_key = f"msg_button_{name}"
                if toggle_key not in st.session_state:
                    st.session_state[toggle_key] = False
                current_open = bool(st.session_state.get(toggle_key, False))
                arrow = "▼" if current_open else "▶"
                if st.button(f"{arrow} {header}", key=button_key):
                    current_open = not current_open
                    st.session_state[toggle_key] = current_open

                if current_open:
                    container = st.container(border=True)
                    with container:
                        if msg_def is not None:
                            meta_cols = st.columns(3)
                            meta_cols[0].caption(f"Frame ID: 0x{int(msg_def.frame_id):X}")
                            meta_cols[1].caption(f"DLC: {msg_def.length}")
                            senders = ", ".join(msg_def.senders) if msg_def.senders else "Unknown"
                            meta_cols[2].caption(f"Senders: {senders}")

                        df_rows = pd.DataFrame(table_rows)
                        st.dataframe(df_rows, hide_index=True, width="stretch")

    st.divider()
    st.subheader("Plot signals over time")
    # Build selectable list of all signals known from db
    all_signal_fqn: List[str] = []
    for m in st.session_state.db.db.messages:  # type: ignore
        for s in m.signals:
            all_signal_fqn.append(f"{m.name}.{s.name}")
    selected = st.multiselect("Signals to plot", options=sorted(all_signal_fqn), default=st.session_state.plot_signals)
    st.session_state.plot_signals = selected
    plot_series = st.session_state.plot_buf.snapshot(selected, max_points=600)
    if not plot_series:
        st.info("No data yet for selected signals.")
    else:
        fig = build_signal_plot(plot_series)
        st.plotly_chart(fig, width="stretch")

    with st.expander("Raw frames (last 300)"):
        raw_rows = st.session_state.get("rx_raw", [])
        if raw_rows:
            rdf = pd.DataFrame(raw_rows).sort_values("timestamp", ascending=False)
            # Drop timestamp column before display
            rdf = rdf.drop(columns=["timestamp"])
            st.dataframe(rdf, width="stretch", height=240)
        else:
            st.caption("No raw frames captured yet.")

# Auto-refresh trigger (at the end to ensure all UI is rendered first)
if st.session_state.connected and st.session_state.get("auto_refresh", True):
    interval_ms = st.session_state.get("refresh_interval", 1000)
    time.sleep(float(interval_ms) / 1000.0)
    st.rerun()
