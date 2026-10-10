# LogLensAI run and deployment guide

Verified against branch feature/jenkins-reference-log-pull in both repositories. This guide describes the local simulation and a separate, unverified production proposal.

## Runtime and structure

LogLensAI uses Python 3.11+ (enum.StrEnum sets the minimum), FastAPI/Uvicorn, Pydantic v2, Streamlit, HTTPX, pandas, and boto3. The companion ProvisioningPlatformSimulator uses FastAPI/Uvicorn and HTTPX. The app has no database, migrations, lockfile, container definition, service definition, or CI configuration. Python 3.12.14 was used for this review. The app does not load .env files.

## Local setup

Install Python 3.11+ and Git. Extract the demo bundle so its root contains start-demo.ps1, requirements-demo.txt, LogLensAI, and ProvisioningPlatformSimulator. From that root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\requirements-demo.txt
.\start-demo.ps1
```

The wrapper starts four services:

| Service | URL | Entrypoint |
| --- | --- | --- |
| Dummy provisioning server | http://127.0.0.1:8010 | simulator:app |
| Jenkins-compatible local controller | http://127.0.0.1:8020 | jenkins_server:app |
| LogLens API | http://127.0.0.1:8000 | main:app |
| Streamlit UI | http://127.0.0.1:8501 | ui.py |

Open the dashboard at http://127.0.0.1:8501. API docs are at http://127.0.0.1:8000/docs, simulator docs at http://127.0.0.1:8010/docs, and controller docs at http://127.0.0.1:8020/docs. The controller is a local stand-in, not Jenkins itself. Stop services with .\stop-demo.ps1. The wrapper saves PID state and local stdout/stderr logs. Startup waits three seconds but does not poll service health.

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
Invoke-RestMethod http://127.0.0.1:8020/health
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-WebRequest http://127.0.0.1:8501 -UseBasicParsing
```

The first three should report status ok; Streamlit should return 200. If a port is occupied, stop the conflicting process or update the ports and matching URL variables.

To run the repositories separately, create one venv in their parent and install both manifests:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\LogLensAI\requirements.txt -r .\ProvisioningPlatformSimulator\requirements.txt
```

From the parent folder, resolve the venv Python once and keep it available in each shell:

```powershell
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
```

Start each command from its repository folder in a separate shell:
- ProvisioningPlatformSimulator: `& $python -m uvicorn simulator:app --host 127.0.0.1 --port 8010`
- ProvisioningPlatformSimulator: `& $python -m uvicorn jenkins_server:app --host 127.0.0.1 --port 8020`
- LogLensAI: `& $python -m uvicorn main:app --host 127.0.0.1 --port 8000`
- LogLensAI: `$env:VNSP_API_BASE_URL = "http://127.0.0.1:8000"; & $python -m streamlit run ui.py --server.address 127.0.0.1 --server.port 8501`

## Environment variables

| Variable | Consumer | Default | Purpose |
| --- | --- | --- | --- |
| VNSP_API_BASE_URL | Streamlit UI | http://127.0.0.1:8000 | API URL |
| JENKINS_DEMO_URL | LogLens API | http://127.0.0.1:8020 | Local controller URL |
| JENKINS_DEMO_USERNAME / JENKINS_DEMO_TOKEN | API and controller | loglens-demo / demo-jenkins-token | Demo Basic authentication |
| PROVISIONING_SIMULATOR_URL | Controller/pipeline | http://127.0.0.1:8010 | Simulator URL |
| DUMMY_SERVER_USERNAME / DUMMY_SERVER_PASSWORD | Simulator/controller/pipeline | jenkins-reader / demo-server-readonly | Demo read-only identity |
| VNSP_CONFIG | Separate app.py prototype | config/services.json | Optional catalog path |

.env.example documents only the API URL and is not auto-loaded. The credential defaults are fictional and loopback-only. Never reuse them. Production values belong in a managed secrets store.

## API and data flow

The main workflow calls POST /investigate with application, error, optional event time, and optional correlation ID:

```powershell
$body = @{ application = "OrderHub"; error = "ESP unauthorized 401 invalid audience"; event_time = "2026-10-10T19:30:00Z" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/investigate" -ContentType "application/json" -Body $body
```

Streamlit sends the incident to FastAPI. FastAPI authenticates to the local controller with HTTP Basic. The controller resolves one of three seeded flows, then authenticates to each dummy host and retrieves order-scoped JSONL evidence. The UI displays order and downstream requests/responses, call path, logs, cause, checks, and Jenkins metadata.

Event time is carried as context and does not filter logs. Confidence is a substring-match count, not a calibrated score. The app does not read source code or call an LLM. The old GET /diagnostics/{request_id} API is tested, but the legacy scenario UI below a return in ui.py is unreachable. app.py is a separate CloudWatch prototype; its checked-in catalog contains empty regions/log groups.

## Tests and verification

From the LogLensAI repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe -m compileall -q main.py models.py diagnostics.py mock_data.py ui.py app.py tests
.\.venv\Scripts\python.exe -m pip check
```

This review ran 32 tests successfully on Python 3.12.14 with one Starlette deprecation warning. Compileall passed. All three API health endpoints returned 200, the Streamlit root returned 200, and the Premise and Hosted API investigations returned four and five reference log entries. The simulator has no automated tests. No real Jenkins, AWS, or production deployment was run.

## Production architecture proposal

```text
Browser --HTTPS + corporate identity--> IIS or approved reverse proxy
                                           |
                                           v
                                Streamlit UI -> FastAPI API
                                                   |
                                      authenticated job request
                                                   v
                                          Jenkins agent
                                                   |
                               least-privilege read-only log adapter
                                                   v
                                  validated, redacted evidence
```

Keep the simulator and local controller isolated. Production would need identity-aware access, TLS, per-app authorization, durable audit state if history is needed, and a log adapter in the approved network zone. None exists in this repository. Do not expose the current app directly to the internet.

### Windows Server proposal (not verified)

The Python apps can run on Windows, but no Windows service definition exists. Use a dedicated service identity, Python 3.11+, Git, a managed secrets source, a TLS certificate, IIS URL Rewrite/ARR, and WebSocket support for Streamlit. Bind Uvicorn and Streamlit to loopback and deny external access to ports 8000 and 8501. Configure IIS to terminate HTTPS, proxy only to loopback, require corporate login, set request/time limits, and allow approved networks on 443.

Deploy into versioned release folders. Example preparation:

```powershell
$release = "C:\Apps\LogLensAI\releases\2026-10-10.1"
New-Item -ItemType Directory -Force $release | Out-Null
git clone --branch feature/jenkins-reference-log-pull --single-branch https://github.com/SrivasthaviDeveloper/LogLensAI.git "$release\LogLensAI"
git clone --branch feature/jenkins-reference-log-pull --single-branch https://github.com/SrivasthaviDeveloper/ProvisioningPlatformSimulator.git "$release\ProvisioningPlatformSimulator"
py -3.11 -m venv "$release\.venv"
& "$release\.venv\Scripts\python.exe" -m pip install -r "$release\LogLensAI\requirements.txt" -r "$release\ProvisioningPlatformSimulator\requirements.txt"
& "$release\.venv\Scripts\python.exe" -m pip check
```

Do not install the fake simulator/controller as a production log source. Current LogLens expects immediate JSON from buildWithParameters. Real Jenkins queues builds and returns queue metadata. A production adapter must poll or receive completion, fetch the archived artifact, validate and redact it, and return the result. That integration is missing.

Use an approved Windows service wrapper or an at-startup Scheduled Task running as the service account. Launch without --reload, inject secrets outside source/command-line arguments, redirect logs into an ACL-protected directory, and configure restart-on-failure. Restart both processes after switching to a new release; roll back by pointing the supervisor back to the last known-good directory. These operations were not tested.

Monitor UI/API separately from Jenkins/log-source readiness. /health only verifies the API process. Add structured redacted logs and dependency health probes. The demo stores no incident history; protect future audit/database data under retention rules. Back up code, job configuration, and non-secret approved config; never commit secrets.

### Jenkins and deployment checklist

The simulator Jenkinsfile is a log-pull pipeline, not a LogLens deployment pipeline. It uses credential ID dummy-server-reader and archives reference-logs.json. No Jenkins job was executed. Jenkins is suitable for CI: checkout, install pinned dependencies, run tests and security scans, package a release, and gate deployment on approval.

- [ ] Confirm OS, identity provider, network zones, and real log source.
- [ ] Replace demo credentials with managed identities and secrets.
- [ ] Add app/API authentication and per-application authorization.
- [ ] Configure HTTPS proxy, WebSockets, firewall rules, and request limits.
- [ ] Implement real Jenkins queue/build/result and artifact retrieval.
- [ ] Add time filtering, correlation matching, redaction, audit, and rate limits.
- [ ] Add simulator, Jenkins, and AWS prototype tests.
- [ ] Pin dependencies and scan a repeatable artifact.
- [ ] Load-test and validate in staging.
- [ ] Configure monitoring, retention, backups, rollback, and security review.

