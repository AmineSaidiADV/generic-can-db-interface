#!/bin/bash
# Quick test script for the CAN DB Interface app

set -e

echo "=================================="
echo "CAN DB Interface - Quick Test"
echo "=================================="
echo ""

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Creating..."
    python3 -m venv .venv
fi

# Activate venv
echo "✓ Activating virtual environment..."
source .venv/bin/activate

# Install dependencies if needed
if ! python -c "import streamlit" 2>/dev/null; then
    echo "✓ Installing dependencies..."
    pip install -q -r requirements.txt
fi

# Check if can0 has traffic
echo ""
echo "✓ Checking CAN bus traffic..."
if timeout 1 candump can0 2>/dev/null | head -3 | grep -q "can0"; then
    echo "  ✅ CAN traffic detected on can0"
else
    echo "  ⚠️  WARNING: No CAN traffic detected on can0"
    echo "     The app will run but won't show messages"
fi

echo ""
echo "=================================="
echo "Starting Streamlit app..."
echo "=================================="
echo ""
echo "📋 IMPORTANT SETUP STEPS:"
echo "   1. Load KCD file:"
echo "      /home/amin/Documents/ADVANTICS/charge-controllers-workspace/Applications/etka-bms/etka/bms/advantics/v2/Advantics_Generic_PEV_protocol_v2.kcd"
echo ""
echo "   2. Select node: Advantics_Charge_Controller"
echo ""
echo "   3. Change channel from 'vcan0' to 'can0'"
echo ""
echo "   4. Click 'Connect'"
echo ""
echo "   5. Go to 'Monitor & Plot' tab"
echo ""
echo "   6. UNCHECK 'Hide producer messages' to see EVSE_Information"
echo ""
echo "   7. Ensure 'Auto-refresh monitor' is CHECKED in sidebar"
echo ""
echo "=================================="
echo ""

streamlit run app.py
