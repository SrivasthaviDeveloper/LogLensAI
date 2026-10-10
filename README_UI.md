# VNSP Troubleshooter Dashboard

The Streamlit dashboard in `ui.py` includes an incident intake form for Application Name, Action Name, and a complete error log message. Its collapsed Advanced options section accepts service/component, environment, request/correlation ID, and event time. It gives an initial, local heuristic assessment from the action and supplied log text, then offers the existing fabricated-scenario workflow through the FastAPI backend. The intake form does not send messages to the API or query AWS.

Application Name choices come from `config/services.json`. The Action Name and Full Error Log Message are combined for the initial assessment, and the complete submitted message can be expanded in the results. Remove secrets and personal data before entering log content.

## API address

`VNSP_API_BASE_URL` selects the backend base URL. The local default is `http://127.0.0.1:8000`.

PowerShell override example:

```powershell
$env:VNSP_API_BASE_URL = "http://127.0.0.1:8000"
```

## Run in VS Code

Open two PowerShell terminals in the project directory. In both terminals, use the virtual environment's Python directly; activation is optional.

Terminal 1, backend:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Terminal 2, dashboard:

```powershell
.\.venv\Scripts\python.exe -m streamlit run ui.py
```

Open the Streamlit URL shown in the terminal (normally `http://localhost:8501`). The FastAPI interactive documentation is at `http://127.0.0.1:8000/docs`.

## Verify integration tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```
