# Generic CAN DB Interface

A user-friendly Streamlit app to load a CAN database (.kcd via cantools), configure and send messages for which a selected node is the producer, and visualize non-producer messages with live plots over time.

## Features
- Load .kcd CAN database with cantools
- Select "Our node" to identify producer vs non-producer messages
- Configure CAN bus (socketcan) and connect
- Send producer messages one-shot or periodically
- Live monitor of received messages with decoded signals
- Plot selected signal values over time

## Requirements
- Python 3.10+
- Linux with socketcan (vcan or physical CAN)
- Dependencies: see `requirements.txt`

## Quick start
1. Create a virtual CAN interface (optional):

```bash
# One-time setup (requires sudo)
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
```

2. Install dependencies and run the app:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

3. In the app:
- Enter the path to a .kcd file (e.g. `../CAN_Databases/example.kcd`)
- Choose your node (producer)
- Configure CAN interface (e.g., interface: `socketcan`, channel: `vcan0`)
- Connect and start sending/monitoring

## Notes
- The app uses `python-can` to interface with CAN and `cantools` for encode/decode.
- On Linux, use `socketcan` with a `canX` or `vcanX` channel.
- Plotting uses Streamlit’s built-in charts.

## Project layout
- `app.py`: Streamlit UI
- `can_backend.py`: CAN bus connection, send/receive, periodic send support
- `db_loader.py`: Load KCD and query nodes/messages
- `plotting.py`: Helpers to maintain time series and build data frames
- `tests/test_load_db.py`: Minimal test to verify KCD parsing

## License
Internal project.
