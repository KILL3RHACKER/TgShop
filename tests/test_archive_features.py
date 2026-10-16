import zipfile
import io
import asyncio
from web.services import parse_accounts_from_archive

sample_log = """
================================
Name: KENNETH ASPLUND
DOB 1971/09/21
SSN: 390764810
Address: 1206 WHITETAIL ST SPOONER WI 54801
Account number: 1340037145970
routing number: 053101121
Saving
Account number: 4340010796113
[{"domain":"sc-static.net","expirationDate":"1767444244194","hostOnly":false,"name":"X-AB","path":"/scevent.min.js","secure":true,"value":"83f59cad16"}]
================================
Name: SARAH CONNOR
DOB 1985/05/12
Address: 456 MAIN ST LOS ANGELES CA 90001
SSN: 123456789
"""

def run_tests():
    # Test 1: Single archive with bank logs and cookie json
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("truist_dump.txt", sample_log)
    zip_buf.seek(0)

    accounts, count = parse_accounts_from_archive("logs.zip", zip_buf.read())
    print(f"Test 1: Extracted {len(accounts)} accounts from bank log:")
    assert len(accounts) == 2, f"Expected 2 accounts, got {len(accounts)}"
    for a in accounts:
        print("  ->", a["data"], "| raw_data len:", len(a["raw_data"]))
        assert len(a["name"]) < 100
        assert not a["name"].startswith("[")
        assert not a["name"].startswith("=")

    assert accounts[0]["state"] == "WI"
    assert accounts[0]["name"] == "KENNETH ASPLUND"
    assert accounts[0]["date_info"] == "21.09"
    assert "SSN: 390764810" in accounts[0]["raw_data"]
    assert "sc-static.net" in accounts[0]["raw_data"]

    assert accounts[1]["state"] == "CA"
    assert accounts[1]["name"] == "SARAH CONNOR"
    assert accounts[1]["date_info"] == "12.05"

    # Test 2: Nested archive (zip inside zip)
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as zf_inner:
        zf_inner.writestr("inner_acc.txt", "NY | MICHAEL CORLEONE | 15.08\n")
    inner_buf.seek(0)

    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w") as zf_outer:
        zf_outer.writestr("inner.zip", inner_buf.read())
    outer_buf.seek(0)

    nested_accs, _ = parse_accounts_from_archive("outer.zip", outer_buf.read())
    print(f"Test 2: Nested archive extracted {len(nested_accs)} accounts:")
    assert len(nested_accs) == 1
    assert nested_accs[0]["data"] == "NY | MICHAEL CORLEONE | 15.08"
    print("  ->", nested_accs[0]["data"])

    print("ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
