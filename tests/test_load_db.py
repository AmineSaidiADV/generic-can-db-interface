from pathlib import Path

from etka_generic_can_db_interface.db_loader import load_kcd


def test_load_example_db():
    # Try a common example from the workspace; adjust the path if needed
    here = Path(__file__).resolve().parents[1]
    kcd = here / "../CAN_Databases/Advantics_Generic_EVSE_protocol_v2.kcd"
    db = load_kcd(kcd)
    assert db.nodes, "Should have nodes in DB"
    assert db.messages, "Should have messages in DB"
