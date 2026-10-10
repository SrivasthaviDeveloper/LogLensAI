# Streamlit UI workflow

`ui.py` now submits the incident intake form to the FastAPI background investigation API. Required inputs are Application Name and Error Message / Error Description. Optional inputs include action, environment, request ID, event time, server, service, error code, and diagnostic context. Common secret formats are redacted by the validated request model before the text is used by classification or reports.

The Validate button receives an investigation ID immediately, then opens a Streamlit dialog that polls `GET /investigations/{id}`. Its stage and progress values come from the backend job. On completion, the UI fetches the report and shows classifier similarity candidates separately from services confirmed by retrieved log records. Reports identify whether the source was local fictional mock logs or configured SSH/SFTP.

The Simulation Lab is a separate page. Generate a scenario there first, copy its returned request ID, then investigate it from the intake page. Both pages use `VNSP_API_BASE_URL`, defaulting to `http://127.0.0.1:8000`.

Run FastAPI and Streamlit in two PowerShell terminals from the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

```powershell
$env:VNSP_API_BASE_URL = "http://127.0.0.1:8000"
$env:VNSP_LOG_SOURCE = "mock"
.\.venv\Scripts\python.exe -m streamlit run ui.py
```

All default logs and training examples are fabricated. SSH/SFTP is optional and disabled until a local source configuration is explicitly authorized; see [README.md](README.md#optional-mock-ubuntusshsftp-source).
