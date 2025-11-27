# Troubleshooting CAN Message Reception

## Problem: Monitor & Plot not showing messages

### Root Cause
The application was applying hardware CAN filters that **only allowed messages with declared producers (senders)** in the KCD file. If your external controller sends messages where the `senders` field is empty/missing in the KCD, those messages were **blocked at the hardware level** before reaching the application.

### Solution Applied
1. **Changed filter logic**: Now includes ALL messages defined in the DB, regardless of sender declaration
2. **Added UI control**: Checkbox "Apply CAN filters (only DB messages)" in sidebar allows you to:
   - ✅ **Enabled (default)**: Receive only CAN IDs defined in your KCD database
   - ❌ **Disabled**: Receive ALL CAN traffic (useful for debugging or unknown messages)

## Verification Steps

### 1. Check CAN Bus Activity
Before starting the app, verify your CAN bus has traffic:

```bash
# Install can-utils if not present
sudo apt-get install can-utils

# Monitor raw CAN traffic
candump can0

# You should see messages like:
#  can0  600   [6]  00 01 02 03 04 05
```

### 2. Test with Filters Disabled
1. Start the app: `streamlit run app.py`
2. Load your KCD file
3. Select node
4. **Uncheck** "Apply CAN filters (only DB messages)"
5. Connect to can0
6. Check "Monitor & Plot" tab - you should see:
   - **Raw frames expander**: Shows ALL CAN traffic (even unknown IDs)
   - **Decoded messages**: Shows messages that match the KCD definitions
   - **Metrics**: "Decoded frames (this refresh)" and "Raw frames (this refresh)" should increment

### 3. Test with Filters Enabled
1. Disconnect
2. **Check** "Apply CAN filters (only DB messages)"
3. Connect again
4. Now only messages defined in your KCD will appear in Raw frames
5. Decoded messages table should show properly parsed signals

## Common Issues

### Still no messages in Monitor?

**Issue**: Auto-refresh not working
- **Solution**: In sidebar, ensure "Auto-refresh monitor" is checked
- Adjust "Interval (ms)" if needed (default 1000ms)

**Issue**: Messages filtered by "Hide producer messages"
- **Solution**: Uncheck "Hide producer messages" in Monitor tab
- This checkbox hides messages where YOUR selected node is the sender

**Issue**: Decode failures
- **Symptom**: Raw frames show data, but decoded table is empty
- **Cause**: DLC mismatch or signal definitions don't match actual data
- **Solution**: Check Raw frames expander to see actual data length vs expected

**Issue**: Wrong KCD loaded
- **Solution**: Verify the KCD path in sidebar matches your controller's protocol
- Example: Use `Advantics_Generic_PEV_protocol_v2.kcd` if you're acting as PEV
- Use `Advantics_Generic_EVSE_protocol_v2.kcd` if you're acting as EVSE

### socketcan loopback
The app enables `receive_own_messages=True` for socketcan, so you'll see your own transmissions. This is intentional for testing.

## Debug Workflow

1. **Start simple**: Disable filters, check Raw frames
2. **Verify decode**: Check if frame IDs in Raw match your KCD
3. **Enable filters**: Once working, enable filters for cleaner view
4. **Check senders**: Verify your KCD has correct `senders` for all messages

## KCD Database Structure

Your messages should look like this in the KCD:
```xml
<Message id="0x600" name="EVSE_Information" length="6">
  <Producer>
    <NodeRef id="1"/>  <!-- If missing, message WAS being filtered out -->
  </Producer>
  <Signal name="..." />
</Message>
```

If `<Producer>` is missing or empty, the OLD code would filter it out. The NEW code accepts it.
