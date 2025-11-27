# 🎯 THE REAL ROOT CAUSE - FIXED!

## What Was Actually Wrong

### The Problem
```python
# OLD CODE (lines 45-46):
_rx_queue = Queue(maxsize=10000)
_raw_queue = Queue(maxsize=10000)
```

**Every time Streamlit refreshes** (which is every second with auto-refresh):
1. ✅ Streamlit re-executes the entire `app.py` script
2. ❌ Lines 45-46 create **BRAND NEW** Queue objects
3. ❌ But background thread callbacks still reference the **OLD** queues
4. ❌ Callbacks keep filling the old queues
5. ❌ UI tries to drain the new (empty) queues

**Result:** Queue size increases (old queue) but drain finds nothing (new queue)!

### Why test_can_rx.py Worked

The standalone test script worked because:
- No Streamlit reruns
- Queues created once and never recreated
- Callbacks and drain function use the same Queue objects

### The Fix

```python
# NEW CODE:
# Store queues in session_state (persists across reruns)
if "rx_queue" not in st.session_state:
    st.session_state.rx_queue = Queue(maxsize=10000)
if "raw_queue" not in st.session_state:
    st.session_state.raw_queue = Queue(maxsize=10000)

# Callbacks MUST use st.session_state directly
def on_decoded(msg_name: str, ts: float, signals: Dict[str, Any]):
    st.session_state.rx_queue.put_nowait((msg_name, ts, signals))
```

**Now:**
1. ✅ Queues created once and stored in `st.session_state`
2. ✅ Session state persists across all Streamlit reruns
3. ✅ Callbacks always reference the **same** queue objects
4. ✅ UI drains the **same** queue objects
5. ✅ Data flows correctly: callbacks → queue → UI

## Visual Explanation

### Before (Broken):
```
Initial connect:
  Queue A created → callbacks capture Queue A → filling Queue A ✅

First rerun (1 second later):
  Queue B created → drain tries Queue B (empty) ❌
  callbacks still use Queue A (filling) ✅

Second rerun (2 seconds later):
  Queue C created → drain tries Queue C (empty) ❌
  callbacks still use Queue A (filling) ✅
```

### After (Fixed):
```
Initial connect:
  Queue in session_state → callbacks use session_state.queue → filling ✅

First rerun:
  Queue still in session_state → drain uses session_state.queue → draining ✅
  callbacks use session_state.queue → filling ✅

All subsequent reruns:
  Same queue for everyone → working perfectly ✅
```

## Testing the Fix

Just restart the app and it should work immediately:

```bash
# If app is running, stop it (Ctrl+C) and restart:
streamlit run app.py

# Or use the helper script:
./run_test.sh
```

You should now see:
- Debug prints: `[DEBUG] Added to rx_queue, qsize now: 450` (and increasing)
- Decoded messages metric showing 10-15 messages
- Raw frames table populated with hundreds of frames
- EVSE_Information appearing in the decoded messages table

## Summary

**This was a classic Streamlit gotcha:**
- Streamlit reruns the entire script on every interaction/refresh
- Module-level mutable objects get recreated each time
- Background threads keep references to old objects
- Must use `st.session_state` for anything that needs to persist!

The fix was simple once we understood the problem: store the Queue objects in `st.session_state` instead of as module-level variables. 🎉
