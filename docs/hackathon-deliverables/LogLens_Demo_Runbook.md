# LogLensAI five-minute live demo runbook

This five-minute demo follows the presentation but focuses on one user journey. All example data are fictional. The standard bundle uses http://127.0.0.1:8501. For the currently running preview from this workspace, use http://127.0.0.1:18501.

## Three demo scenarios

| Scenario | Exact input | Expected result | Feature shown |
| --- | --- | --- | --- |
| Premise credential failure (recommended) | Application OrderHub; error ESP unauthorized 401 invalid audience | ORD-73106, Premise, FAILED; route premise-site-agent to ESP credential-broker; order 401 and downstream INVALID_AUDIENCE; two servers and four logs | End-to-end route, both request/response boundaries, Jenkins-authenticated log retrieval, next checks |
| Hosted capacity failure | Application OrderHub; error IPSM IP_POOL_EXHAUSTED 409 | ORD-48291, Hosted, FAILED; hosted-order-orchestrator to IPSM ip-allocation-service; 409; two servers and five logs | Hosted path, dependency error and fixture logs |
| Hosted timeout after late success | Application OrderHub; error IPSM request timed out 504 | ORD-59012, Hosted, DEGRADED; caller 504 after a 2-second deadline while IPSM reports 200 after 2438 ms | Timeout diagnosis and retry/idempotency risk |

The timeout flow demonstrates why a caller timeout does not prove that a downstream operation failed. The app's confidence is substring-based. The optional event time is shown as context, not used to filter logs.

## Main demo objective

Show how a sanitized incident becomes a traceable, fictional order path with explicit request/response evidence and reference log entries. Audience takeaway: LogLens organizes evidence around the failing service boundary; the current build is a deterministic simulation, not a production log intelligence system.

### Preparation

1. Extract the demo bundle and open PowerShell at its root.
2. Run .\start-demo.ps1 and confirm the four service health checks in the run/deploy guide.
3. Open the dashboard. If the prior session shows results, reload before beginning.
4. Keep the example text below ready to paste. Never enter real tokens, customer data, or production log excerpts.
5. Keep the API fallback command ready in case Streamlit is slow.

## Timestamped five-minute script

| Time | Action and exact input | Expected output | Say this |
| --- | --- | --- | --- |
| 00:00–00:30 | Open the dashboard and point out Incident intake. | Application dropdown, action field, error field, optional Advanced options. | “This demo uses a fictional provisioning platform. I’ll trace one premise activation failure from the order boundary through its credential dependency.” |
| 00:30–01:15 | Set Application Name to OrderHub. Leave Action Name blank. Enter Full Error Log Message: ESP unauthorized 401 invalid audience. In Advanced options enter Event time: 2026-10-10T19:30:00Z. | Form contains the error and optional time; no secrets. | “The input is the application, a sanitized message, and an optional event time. The time is carried into the result, but the current fixture lookup does not filter by it.” |
| 01:15–02:00 | Click Analyze incident once and wait for Investigation findings. | Order ORD-73106, Premise, FAILED, high match confidence; Jenkins reports a successful build, two dummy servers, and four entries. | “The deterministic demo selects a seeded Premise flow. Jenkins authenticates to the local job endpoint, and the job uses a separate read-only account for the dummy servers.” |
| 02:00–03:00 | Point to Call path and Likely explanation. | OrderHub premise-site-agent on premise-order-srv-02 calls ESP credential-broker on esp-integration-srv-01. Cause identifies an audience mismatch. | “We can see the order service and its downstream owner. The response is an HTTP 401 because the sample audience differs from the expected premise audience.” |
| 03:00–04:15 | Compare Order API request/response and Downstream ESP request/response. | Order API responds DOWNSTREAM_AUTH_REJECTED 401. ESP returns INVALID_AUDIENCE and expectedAudience premise-provisioning-demo. | “The two boundaries make the failure specific: the order handler propagated the credential broker rejection. This is more useful than stopping at the final 401.” |
| 04:15–05:00 | Expand Jenkins-pulled dummy server logs, then point to Suggested next checks and close. | Four timestamped entries from both servers; next checks call for audience/identity/scope comparison. | “The logs support the same explanation and show which hosts supplied the evidence. This demo turns a message into a reviewable path and next checks, while all servers and credentials remain fictional.” |

Durations: 30 + 45 + 45 + 60 + 75 + 45 = 300 seconds.

## Exact fallback

If the browser page is slow or the result panel does not appear, use the LogLens API from PowerShell:

```powershell
$body = @{
  application = "OrderHub"
  error = "ESP unauthorized 401 invalid audience"
  event_time = "2026-10-10T19:30:00Z"
} | ConvertTo-Json
$result = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/investigate" -ContentType "application/json" -Body $body
$result.order
$result.request
$result.response
$result.downstream_request
$result.downstream_response
$result.jenkins
$result.logs
```

If port 8000 differs, update the URL to the configured API URL. If this returns 503, check API /health, controller /health at port 8020, and simulator /health at port 8010. If it returns 502, check that simulator log endpoints and the controller can reach each other. Do not spend the live demo changing credentials.

If the sample selects the wrong flow, use the exact application/error pair above and avoid adding ambiguous terms such as timeout or pool. Reload the form and resubmit once. The selection uses substring hits, so results may be ambiguous with other text.

## Technical meaning

- The “Jenkins build” is a local Jenkins-compatible controller response. It is not evidence that a real Jenkins controller ran.
- “Authenticated servers” means HTTP Basic authentication to the local simulator, not SSH or production access.
- Event time appears in context but is not used to filter fixture timestamps.
- Request/response payloads and logs are authored demo evidence, not live network captures.
- No LLM is called. Matching is a small deterministic set of keyword rules.

The UI may be reset by reloading the page. The wrapper can be stopped after the demo with .\stop-demo.ps1.

