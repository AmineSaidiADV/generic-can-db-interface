# How to Use the CAN DB Interface App

## The Backend Works! ✅

The test script (`test_can_rx.py`) confirms:
- **530 frames received** in 5 seconds from can0
- **529 messages decoded** successfully
- **EVSE_Information (0x600)** received every ~100ms as expected
- All callbacks and queues functioning correctly

## Running the Streamlit App

### 1. **Set up Python Environment**

```bash
cd /home/amin/Documents/ADVANTICS/charge-controllers-workspace/Applications/etka-generic-can-db-interface

# Create virtual environment if not exists
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. **Run the App**

```bash
# Make sure venv is activated
source .venv/bin/activate

# Run Streamlit
streamlit run app.py
```

The app will open in your browser (usually http://localhost:8501)

### 3. **Configure the App**

In the left sidebar:

#### **Load Database**
1. **KCD path**: Enter the full path to your KCD file:
   ```
   /home/amin/Documents/ADVANTICS/charge-controllers-workspace/Applications/etka-bms/etka/bms/advantics/v2/Advantics_Generic_PEV_protocol_v2.kcd
   ```
2. Click **"Load DB"**
3. You should see: `✅ Loaded DB: Advantics_Generic_PEV_protocol_v2.kcd`

#### **Select Node**
- Choose **"Advantics_Charge_Controller"** from the dropdown (this is the producer of EVSE_Information)
- Or choose **"Vehicle"** if you want to send vehicle messages

#### **Configure CAN Bus**
- **Interface**: `socketcan`
- **Channel**: `can0`
- **Apply CAN filters**: ✅ **CHECKED** (to receive only DB messages)
  - If you want to see ALL CAN traffic, uncheck this

#### **Connect**
1. Click **"Connect"**
2. Wait a moment - you should see the button change to "Disconnect"

### 4. **Monitor Messages**

Click the **"Monitor & Plot"** tab

You should see:
- **Metrics at top**: 
  - "Decoded frames (this refresh)" - should show 10-50+ per refresh
  - "Raw frames (this refresh)" - should match or exceed decoded
  
- **Decoded messages table**:
  - Shows all messages with their latest signal values
  - **EVSE_Information** should appear with:
    - Communication_Stage = "Waiting_For_EVSE"
    - Max_Current = 0
    - etc.
  
- **"Hide producer messages" checkbox**:
  - ⚠️ **IMPORTANT**: If you selected "Advantics_Charge_Controller" as your node, and this box is CHECKED, EVSE_Information will be HIDDEN (because it's a producer message)
  - **UNCHECK this box** to see EVSE_Information!

- **Raw frames expander** (click to expand):
  - Shows last 500 raw CAN frames
  - Should see ID 0x600 appearing repeatedly

### 5. **Troubleshooting**

#### No messages appearing?

**Check #1: Auto-refresh**
- Scroll down in sidebar to "Refresh" section
- Ensure **"Auto-refresh monitor"** is ✅ CHECKED
- Set interval to 1000ms

**Check #2: Hide producer messages**
- In Monitor tab, **UNCHECK** "Hide producer messages"

**Check #3: Raw frames**
- Click "Raw frames" expander
- If you see frames here but not in decoded table → decode issue
- If you see NO frames here → connection/filter issue

**Check #4: Filters**
- Disconnect
- In sidebar, **UNCHECK** "Apply CAN filters"
- Connect again
- Now you should see ALL CAN traffic

**Check #5: Verify can0 traffic**
In a separate terminal:
```bash
candump can0 | grep 600
```
You should see `can0  600   [6]  01 00 00 00 00 00` every 100ms

## Expected Behavior

When working correctly, you should see:
- Metrics updating every second showing 10-50 frames
- Decoded messages table with 10-15 different messages
- **EVSE_Information** row showing:
  - Communication_Stage value
  - Max_Current value
  - Protocol, Pins, etc.
- Raw frames table showing hex data for all IDs

## Quick Test

Run the standalone test to verify backend works:
```bash
python3 test_can_rx.py
```

Expected output:
```
================================================================================
CAN Reception Test
================================================================================

1. Loading KCD: ...
   ✓ Loaded 17 messages
   ...

7. Results:
   - Raw frames received: 500+
   - Decoded messages: 500+
```

If this works but Streamlit doesn't show data, the issue is in the UI refresh logic, not the CAN backend.

## Notes

- The app uses **hardware CAN filters** when "Apply CAN filters" is checked
  - Filters are built from ALL messages in the KCD (not just producers)
  - Extended CAN IDs (> 0x7FF) have different mask
  
- **Auto-refresh** is required because:
  - Background thread receives CAN frames → queues
  - UI thread (Streamlit) drains queues → displays
  - Without refresh, queues fill but UI never updates

- **"Hide producer messages"** affects DISPLAY only:
  - Doesn't affect reception or decoding
  - Just filters the table view
  - Useful when you want to see only "received" messages vs "sent" messages
