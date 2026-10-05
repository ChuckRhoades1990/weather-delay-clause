"""Direct-mode tests for WeatherDelayClause (Open-Meteo archive mocked)."""

import json

from tests.direct.conftest import to_hex

C = "contracts/weather_delay_clause.py"
ARCHIVE = r".*archive-api\.open-meteo\.com.*"


def _day(rain, gust, date="2026-09-20"):
    body = {
        "daily": {
            "time": [date],
            "precipitation_sum": [rain],
            "wind_gusts_10m_max": [gust],
        }
    }
    return {"status": 200, "body": json.dumps(body)}


def _job(direct_vm, direct_deploy, contractor, client):
    c = direct_deploy(C)
    direct_vm.sender = contractor
    jid = c.create_job(
        "Bendigo slab pour",
        to_hex(client),
        -367570,
        1442794,
        "Australia/Melbourne",
        50,  # 5.0 mm
        60,  # 60 km/h
        "2026-10-30",
    )
    return c, jid


def test_create_job(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, jid = _job(direct_vm, direct_deploy, direct_alice, direct_bob)
    j = c.get_job(jid)
    assert jid == "job-1"
    assert j["contractor"] == to_hex(direct_alice)
    assert j["client"] == to_hex(direct_bob)
    assert j["lat"] == "-36.7570"
    assert j["lon"] == "144.2794"
    assert j["completion"] == "2026-10-30"
    assert c.get_job_count() == 1


def test_create_job_validation(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = direct_deploy(C)
    direct_vm.sender = direct_alice
    bob = to_hex(direct_bob)
    with direct_vm.expect_revert("Invalid coordinates"):
        c.create_job("Job x", bob, 950000, 0, "Australia/Melbourne", 50, 60, "2026-10-30")
    with direct_vm.expect_revert("Date must be YYYY-MM-DD"):
        c.create_job("Job x", bob, 0, 0, "Australia/Melbourne", 50, 60, "30/10/2026")
    with direct_vm.expect_revert("Thresholds must be positive"):
        c.create_job("Job x", bob, 0, 0, "Australia/Melbourne", 0, 60, "2026-10-30")
    with direct_vm.expect_revert("Client must differ from contractor"):
        c.create_job("Job x", to_hex(direct_alice), 0, 0, "Australia/Melbourne", 50, 60, "2026-10-30")


def test_rain_day_extends_completion(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, jid = _job(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.mock_web(ARCHIVE, _day(12.4, 30.0))
    rec = c.claim_delay(jid, "2026-09-20")
    assert rec["approved"] is True
    assert rec["reason"] == "rain"
    assert rec["rain_x10"] == 124
    j = c.get_job(jid)
    assert j["completion"] == "2026-10-31"
    assert j["approved_days"] == 1


def test_wind_day_and_dry_day(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, jid = _job(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.mock_web(ARCHIVE, _day(0.0, 72.0, "2026-09-21"))
    assert c.claim_delay(jid, "2026-09-21")["reason"] == "wind"
    direct_vm.clear_mocks()
    direct_vm.mock_web(ARCHIVE, _day(1.2, 20.0, "2026-09-22"))
    rec = c.claim_delay(jid, "2026-09-22")
    assert rec["approved"] is False
    assert rec["reason"] == "below thresholds"
    assert c.get_job(jid)["completion"] == "2026-10-31"
    assert len(c.get_claims(jid)) == 2


def test_duplicate_and_missing_data(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, jid = _job(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.mock_web(ARCHIVE, _day(9.0, 10.0))
    c.claim_delay(jid, "2026-09-20")
    with direct_vm.expect_revert("Date already claimed"):
        c.claim_delay(jid, "2026-09-20")
    direct_vm.clear_mocks()
    direct_vm.mock_web(ARCHIVE, _day(None, None, "2026-10-04"))
    with direct_vm.expect_revert("No weather data for that date"):
        c.claim_delay(jid, "2026-10-04")


def test_permissions_and_lock(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, jid = _job(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the contractor can claim"):
        c.claim_delay(jid, "2026-09-20")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Only the client can lock"):
        c.lock(jid)
    direct_vm.sender = direct_bob
    c.lock(jid)
    assert c.get_job(jid)["locked"] is True
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Job is locked"):
        c.claim_delay(jid, "2026-09-20")
