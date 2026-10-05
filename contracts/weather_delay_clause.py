# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
WeatherDelayClause — on-chain "weather day" claims for construction and trade jobs.

Building and trade contracts usually let the contractor extend the completion
date for days lost to rain or high wind. In practice these claims are argued
over by email weeks later. This contract makes the clause self-enforcing:

  * The contractor and client agree a job site (lat/lon), a completion date
    and the weather thresholds that count as a lost day.
  * For any working day, the contractor calls `claim_delay(job_id, date)`.
  * Validators fetch the recorded daily weather for that site and date from
    the Open-Meteo historical archive (a public JSON API), parse it
    deterministically and agree on the exact numbers with `strict_eq`.
  * If rainfall or wind gusts meet the agreed thresholds, the claim is
    approved and the completion date moves out by one day. Otherwise it is
    recorded as rejected, with the measured values, so nobody can re-argue it.

Design notes
  * No LLM is involved: this is a pure web-data contract, so consensus uses
    the strictest and cheapest principle (`gl.eq_principle.strict_eq`).
    Every validator calls the same archive endpoint and must return the
    identical integer pair.
  * Weather values are stored as integers (rain in tenths of a mm, gusts in
    whole km/h) so on-chain state stays deterministic.
  * Each date can be claimed once per job; future or not-yet-published dates
    return null from the archive and revert with "No weather data".
  * Either party can see every claim and its measurements. The client can
    lock the job after practical completion so no further claims are made.
"""
import json
import datetime
from dataclasses import dataclass
from genlayer import *

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MAX_CLAIMS_PER_JOB = 120


def _parse_date(s: str) -> datetime.date:
    try:
        if len(s) != 10:
            raise ValueError()
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise gl.vm.UserError("Date must be YYYY-MM-DD")


def _fmt_coord(e4: int) -> str:
    sign = "-" if e4 < 0 else ""
    a = abs(e4)
    return f"{sign}{a // 10000}.{a % 10000:04d}"


@allow_storage
@dataclass
class Job:
    id: str
    name: str
    contractor: Address
    client: Address
    lat_e4: i64
    lon_e4: i64
    timezone: str
    rain_threshold_x10: u256  # tenths of a mm
    gust_threshold_kmh: u256
    original_completion: str
    completion: str
    approved_days: u256
    locked: bool
    claims: str  # JSON list of claim dicts


class WeatherDelayClause(gl.Contract):
    jobs: TreeMap[str, Job]
    job_count: u256

    def __init__(self):
        self.job_count = u256(0)

    # ── helpers ───────────────────────────────────────────────────────────

    def _get(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise gl.vm.UserError("Job not found")
        return self.jobs[job_id]

    def _fetch_weather(self, lat_e4: int, lon_e4: int, tz: str, day: str) -> dict:
        url = (
            f"{ARCHIVE_URL}?latitude={_fmt_coord(lat_e4)}&longitude={_fmt_coord(lon_e4)}"
            f"&start_date={day}&end_date={day}"
            f"&daily=precipitation_sum,wind_gusts_10m_max&timezone={tz.replace('/', '%2F')}"
        )

        def fetch() -> dict:
            res = gl.nondet.web.get(url)
            if res.status != 200 or res.body is None:
                return {"ok": False, "rain_x10": -1, "gust_kmh": -1}
            data = json.loads(res.body.decode("utf-8"))
            daily = data.get("daily", {})
            rain = (daily.get("precipitation_sum") or [None])[0]
            gust = (daily.get("wind_gusts_10m_max") or [None])[0]
            if rain is None or gust is None:
                return {"ok": False, "rain_x10": -1, "gust_kmh": -1}
            return {
                "ok": True,
                "rain_x10": int(round(float(rain) * 10)),
                "gust_kmh": int(round(float(gust))),
            }

        return gl.eq_principle.strict_eq(fetch)

    # ── writes ────────────────────────────────────────────────────────────

    @gl.public.write
    def create_job(
        self,
        name: str,
        client: str,
        lat_e4: int,
        lon_e4: int,
        timezone: str,
        rain_threshold_x10: int,
        gust_threshold_kmh: int,
        completion: str,
    ) -> str:
        if len(name.strip()) < 3:
            raise gl.vm.UserError("Name too short")
        if not (-900000 <= lat_e4 <= 900000) or not (-1800000 <= lon_e4 <= 1800000):
            raise gl.vm.UserError("Invalid coordinates")
        if "/" not in timezone or " " in timezone:
            raise gl.vm.UserError("Timezone must be IANA, e.g. Australia/Melbourne")
        if rain_threshold_x10 < 1 or gust_threshold_kmh < 1:
            raise gl.vm.UserError("Thresholds must be positive")
        _parse_date(completion)
        client_addr = Address(client)
        if client_addr == gl.message.sender_address:
            raise gl.vm.UserError("Client must differ from contractor")

        self.job_count = u256(int(self.job_count) + 1)
        job_id = f"job-{int(self.job_count)}"
        self.jobs[job_id] = Job(
            id=job_id,
            name=name,
            contractor=gl.message.sender_address,
            client=client_addr,
            lat_e4=i64(lat_e4),
            lon_e4=i64(lon_e4),
            timezone=timezone,
            rain_threshold_x10=u256(rain_threshold_x10),
            gust_threshold_kmh=u256(gust_threshold_kmh),
            original_completion=completion,
            completion=completion,
            approved_days=u256(0),
            locked=False,
            claims="[]",
        )
        return job_id

    @gl.public.write
    def claim_delay(self, job_id: str, date: str) -> dict:
        job = self._get(job_id)
        if gl.message.sender_address != job.contractor:
            raise gl.vm.UserError("Only the contractor can claim")
        if job.locked:
            raise gl.vm.UserError("Job is locked")
        claimed_day = _parse_date(date)
        if claimed_day > _parse_date(job.completion):
            raise gl.vm.UserError("Date is after the completion date")
        claims = json.loads(job.claims)
        if any(c["date"] == date for c in claims):
            raise gl.vm.UserError("Date already claimed")
        if len(claims) >= MAX_CLAIMS_PER_JOB:
            raise gl.vm.UserError("Claim limit reached")

        w = self._fetch_weather(int(job.lat_e4), int(job.lon_e4), job.timezone, date)
        if not w["ok"]:
            raise gl.vm.UserError("No weather data for that date")

        rain_hit = w["rain_x10"] >= int(job.rain_threshold_x10)
        gust_hit = w["gust_kmh"] >= int(job.gust_threshold_kmh)
        approved = rain_hit or gust_hit
        if approved:
            new_done = _parse_date(job.completion) + datetime.timedelta(days=1)
            job.completion = new_done.isoformat()
            job.approved_days = u256(int(job.approved_days) + 1)

        record = {
            "date": date,
            "rain_x10": w["rain_x10"],
            "gust_kmh": w["gust_kmh"],
            "approved": approved,
            "reason": "rain" if rain_hit else ("wind" if gust_hit else "below thresholds"),
        }
        claims.append(record)
        job.claims = json.dumps(claims)
        return record

    @gl.public.write
    def lock(self, job_id: str) -> None:
        job = self._get(job_id)
        if gl.message.sender_address != job.client:
            raise gl.vm.UserError("Only the client can lock")
        job.locked = True

    # ── views ─────────────────────────────────────────────────────────────

    @gl.public.view
    def get_job(self, job_id: str) -> dict:
        j = self._get(job_id)
        return {
            "id": j.id,
            "name": j.name,
            "contractor": j.contractor.as_hex,
            "client": j.client.as_hex,
            "lat": _fmt_coord(int(j.lat_e4)),
            "lon": _fmt_coord(int(j.lon_e4)),
            "timezone": j.timezone,
            "rain_threshold_x10": int(j.rain_threshold_x10),
            "gust_threshold_kmh": int(j.gust_threshold_kmh),
            "original_completion": j.original_completion,
            "completion": j.completion,
            "approved_days": int(j.approved_days),
            "locked": j.locked,
        }

    @gl.public.view
    def get_claims(self, job_id: str) -> list:
        return json.loads(self._get(job_id).claims)

    @gl.public.view
    def get_job_count(self) -> int:
        return int(self.job_count)
