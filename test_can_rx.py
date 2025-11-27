#!/usr/bin/env python3
"""
Quick test script to verify CAN reception with the KCD database.
This bypasses Streamlit to isolate the CAN backend issue.
"""
import time
import sys
from pathlib import Path

# Add the module to path
sys.path.insert(0, str(Path(__file__).parent))

from etka_generic_can_db_interface.db_loader import load_kcd
from etka_generic_can_db_interface.can_backend import CANBackend
import can

print("=" * 80)
print("CAN Reception Test")
print("=" * 80)

# Load the KCD
kcd_path = "/home/amin/Documents/ADVANTICS/charge-controllers-workspace/Applications/etka-bms/etka/bms/advantics/v2/Advantics_Generic_PEV_protocol_v2.kcd"
print(f"\n1. Loading KCD: {kcd_path}")
db = load_kcd(kcd_path)
print(f"   ✓ Loaded {len(db.messages)} messages")
print(f"   ✓ Nodes: {db.nodes}")

# Find EVSE_Information
evse_info = None
for m in db.messages:
    if m.name == "EVSE_Information":
        evse_info = m
        print(f"\n2. Found target message:")
        print(f"   - Name: {evse_info.name}")
        print(f"   - ID: 0x{evse_info.frame_id:X}")
        print(f"   - DLC: {evse_info.length}")
        print(f"   - Senders: {evse_info.senders}")
        print(f"   - Signals: {evse_info.signals}")
        break

if not evse_info:
    print("ERROR: EVSE_Information not found in database!")
    sys.exit(1)

# Setup encode/decode functions
def encode(msg_name: str, sig_vals: dict):
    msg = db.db.get_message_by_name(msg_name)
    data = msg.encode(sig_vals)
    return can.Message(arbitration_id=msg.frame_id, data=data, is_extended_id=msg.is_extended_frame)

def id_to_name(frame_id: int):
    try:
        m = db.db.get_message_by_frame_id(frame_id)
        return m.name
    except Exception:
        return None

def decode(frame_id: int, data: bytes):
    try:
        return db.db.decode_message(frame_id, data)
    except Exception as e:
        print(f"   DECODE ERROR for 0x{frame_id:X}: {e}")
        return None

# Setup backend
print("\n3. Setting up CAN backend")
backend = CANBackend()
backend.set_db_interfaces(encode, decode, id_to_name)

# Build filters
print("\n4. Building filters")
filters = []
for m in db.db.messages:
    mask = 0x1FFFFFFF if m.is_extended_frame else 0x7FF
    filters.append({
        "can_id": int(m.frame_id),
        "can_mask": int(mask),
        "extended": bool(m.is_extended_frame),
    })
print(f"   ✓ Created {len(filters)} filters")
print(f"   ✓ Filter for 0x600: ID=0x600, Mask=0x7FF, Ext=False")

# Counters
decoded_count = 0
raw_count = 0

def on_decoded(msg_name: str, ts: float, signals: dict):
    global decoded_count
    decoded_count += 1
    print(f"\n   [DECODED #{decoded_count}] {msg_name} @ {ts:.3f}")
    for sig, val in signals.items():
        print(f"      - {sig} = {val}")

def on_raw(arb_id: int, data: bytes, ts: float, is_ext: bool):
    global raw_count
    raw_count += 1
    if raw_count <= 5:  # Only print first 5 to avoid spam
        print(f"   [RAW #{raw_count}] 0x{arb_id:X} [{len(data)}] {data.hex(' ')}")

backend.on_decoded(on_decoded)
backend.on_raw(on_raw)

# Connect
print("\n5. Connecting to can0 WITH filters...")
try:
    backend.connect(
        interface="socketcan",
        channel="can0",
        can_filters=filters
    )
    print("   ✓ Connected")
except Exception as e:
    print(f"   ✗ Connection failed: {e}")
    sys.exit(1)

print("\n6. Listening for 5 seconds...")
print("   (Expecting EVSE_Information @ 0x600 every 100ms)")
print("-" * 80)
time.sleep(5)
print("-" * 80)

print(f"\n7. Results:")
print(f"   - Raw frames received: {raw_count}")
print(f"   - Decoded messages: {decoded_count}")

if raw_count == 0:
    print("\n   ❌ NO FRAMES RECEIVED - Filter issue or no traffic")
    print("   Retrying WITHOUT filters...")
    backend.disconnect()
    time.sleep(0.5)
    
    # Reset counters
    raw_count = 0
    decoded_count = 0
    
    backend.connect(
        interface="socketcan",
        channel="can0",
        can_filters=None  # NO FILTERS
    )
    print("   ✓ Connected without filters")
    print("\n   Listening for 5 seconds...")
    print("-" * 80)
    time.sleep(5)
    print("-" * 80)
    
    print(f"\n   Results (no filters):")
    print(f"   - Raw frames received: {raw_count}")
    print(f"   - Decoded messages: {decoded_count}")

backend.disconnect()
print("\n" + "=" * 80)
print("Test complete")
print("=" * 80)
