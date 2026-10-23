# Metric verification — do resource metrics exist in the official Liara API?

**Question (Phase-3 T2):** does the official PaaS API expose CPU/RAM/resource usage?

**Method:** the official OpenAPI spec was downloaded from
`https://developers.liara.ir/spec/paas.yaml` (54,290 bytes, `openapi: 3.0.1`), snapshotted
verbatim at **`config/spec/paas.yaml`** (sha256 prefix `a8b8ab24c0b2decf`, checked 2026-10-20)
and grepped. Raw evidence below — every claim can be re-checked against the snapshot.

## Answer: YES — five `Reports` endpoints exist

| # | Path | Method | Params | 200 shape (from spec) |
|---|------|--------|--------|------------------------|
| 1 | `/v1/projects/{name}/metrics/summary` | GET | none | `{cpuUsage:[{value, applet}], memoryUsage:[{value, applet}], disksUsage:[{size, usage, reportedAt, name}]}` |
| 2 | `/v1/projects/{name}/metrics/cpu` | GET | `since` **required**, number | `Reports {end, result:[{values, applet}]}` |
| 3 | `/v1/projects/{name}/metrics/memory` | GET | `since` **required**, number | `Reports` |
| 4 | `/v1/projects/{name}/metrics/network-receive` | GET | `since` **required**, number | `Reports` |
| 5 | `/v1/projects/{name}/metrics/network-transmit` | GET | `since` **required**, number | `Reports` |

Declared errors on all five: `400 / 401 / 404`. All are tagged `Reports`,
operationIds `getAppSummaryReports` / `getAppCpuReports` / `getAppMemoryReports` /
`getAppNetworkReceiveReports` / `getNetworkTransmitReports`.

### Raw grep evidence (against `config/spec/paas.yaml`)

```
$ rg -n "metrics" config/spec/paas.yaml
1133:  '/v1/projects/{name}/metrics/summary':
1203:  '/v1/projects/{name}/metrics/cpu':
1239:  '/v1/projects/{name}/metrics/memory':
1275:  '/v1/projects/{name}/metrics/network-receive':
1311:  '/v1/projects/{name}/metrics/network-transmit':

$ rg -n "cpuUsage|memoryUsage|disksUsage" config/spec/paas.yaml
1155:                  cpuUsage:        # inside metrics/summary 200 response
1168:                  memoryUsage:
1181:                  disksUsage:

$ rg -n "required: true" config/spec/paas.yaml   # under each metrics/cpu|memory path
1216..1220:  - name: since / in: query / required: true / type: number
```

## What LiaraPilot does with this

- `LiaraApiDeployer.metrics_summary()` performs the read-only `GET …/metrics/summary` call.
- The API is exposed read-only at `GET /api/sessions/{id}/monitor` and (T6) polled periodically.
- **Every metric value rendered in the UI carries an `UNVERIFIED` badge** until a live read
  against the real API succeeds (units/scaling of `value` arrays, meaning of `applet` labels and
  `disksUsage` strings are not observable from the spec — `metrics_values_semantics` in
  `config/defaults.yaml`).

## Related endpoints discovered during the same grep (used by risk matrix / live_check)

```
$ rg -n "^  '/v1/projects" config/spec/paas.yaml
  47:  /v1/projects:                          GET (list apps) + POST (create)
  93:  '/v1/projects/{name}':                 GET details + DELETE (destructive!)
 146:  '/v1/projects/{name}/actions/scale':   POST {scale: 1|0} turn on/off
 183:  '/v1/projects/{name}/actions/restart': POST restart
 472:  '/v1/projects/{name}/resize':          POST {planID} change plan
 629:  '/v1/projects/{id}/disks':             GET list + POST create
 861:  '/v1/projects/{id}/disks/{name}':      DELETE disk (destructive!)
 957:  '/v1/projects/{name}/logs':            GET, `since` REQUIRED (string)
 993:  '/v1/projects/{name}/events':          GET app events
1089:  '/v1/projects/{name}/releases':        GET releases
```

## Plans & prices (Phase-3 T5 answer)

```
$ rg -in "getPlans|/plans|marketplace" config/spec/paas.yaml
(no matches)
$ rg -n "plan" config/spec/paas.yaml | rg -v "planID" | head
# 'plan' appears only as: changePlan body {planID}, create-app schema planID,
# ProjectAllDetails.planID — never as a catalog/list endpoint.
```

→ **Live plan/price reading is impossible via this API.** `data/prices.yaml` (all-UNVERIFIED)
remains the static fallback; the UI announces this limitation explicitly. Plan *changes* are
still possible (`POST /resize` with a console-provided `planID`) and sit in risk tier 2.

## Bug found and fixed during this verification

`GET /v1/projects/{name}/logs` declares `since` **required**; the deployer previously called it
without the param (would 400 live). Fixed: `since=<LIARA_LOGS_SINCE, default "0">` is now sent
(value format remains UNVERIFIED — `logs_since_format`).
