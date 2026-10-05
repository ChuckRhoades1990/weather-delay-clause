# WeatherDelayClause

A GenLayer Intelligent Contract that makes the **"weather day" clause in building and trade contracts self-enforcing**. Validators read recorded weather for the job site straight from a public archive and agree on the numbers. If the day qualifies, the completion date moves out automatically.

## Why
Most construction and trade contracts let the contractor extend the completion date for days lost to rain or high wind. These claims are usually argued over by email weeks later, with screenshots of weather apps as evidence. Here the parties agree the rules once, and the chain applies them to the official record.

## How it works
1. **`create_job(name, client, lat_e4, lon_e4, timezone, rain_threshold_x10, gust_threshold_kmh, completion)`**: the contractor registers the site (coordinates × 10⁴), the client's address, the thresholds that count as a lost day (rain in tenths of a mm, gusts in km/h) and the completion date.
2. **`claim_delay(job_id, date)`**: the contractor claims a day. Each validator calls the [Open-Meteo historical weather API](https://open-meteo.com/en/docs/historical-weather-api) for that site and date (`precipitation_sum`, `wind_gusts_10m_max`) and parses the JSON deterministically.
3. If rain or gusts meet a threshold, the claim is **approved** and `completion` moves out by one day. Otherwise it is stored as **rejected**, with the measured values.
4. **`lock(job_id)`**: the client locks the job at practical completion, and no further claims can be made.

### Consensus design: `gl.eq_principle.strict_eq`
There is no LLM in this contract. It is a pure web-data contract, so it uses the strictest and cheapest equivalence principle: every validator must return the **identical** `{ok, rain_x10, gust_kmh}` integer tuple from the same archive endpoint. Values are rounded to integers before they leave the non-deterministic block, so float formatting can't break agreement.

### Safeguards
- Only the contractor can claim, and only the client can lock.
- Each date can be claimed once per job, and a job allows at most 120 claims.
- A date after the current completion date is rejected.
- If the archive returns `null` (a future date or one not yet published), the claim reverts with *"No weather data for that date"* and nothing is stored.
- Inputs are validated: coordinates, ISO dates, IANA timezone and positive thresholds.

## Methods
| Method | Type |
|---|---|
| `create_job(...) -> str` | write |
| `claim_delay(job_id, date) -> dict` | write (non-deterministic, `strict_eq`) |
| `lock(job_id)` | write, client only |
| `get_job(job_id) -> dict` | view |
| `get_claims(job_id) -> list` | view |
| `get_job_count() -> int` | view |

## Example
Concrete slab in Bendigo, VIC. A day counts as lost at ≥ 5 mm rain or ≥ 60 km/h gusts:
```
create_job("Bendigo slab pour", "0xClient…", -367570, 1442794, "Australia/Melbourne", 50, 60, "2026-10-30")
claim_delay("job-1", "2026-09-20")
→ {"date": "2026-09-20", "rain_x10": 124, "gust_kmh": 30, "approved": true, "reason": "rain"}
```

## Tests
```bash
pip install -r requirements.txt
genvm-lint check contracts/weather_delay_clause.py
python -m pytest tests/direct -v
```
The six direct-mode tests mock the archive API. They cover job creation, input validation, rain and wind approvals, a dry-day rejection, duplicate and missing-data reverts, and permissions and locking.

## Deployment
GenLayer Studio (studionet): see the explorer link in the submission.

## License
MIT
