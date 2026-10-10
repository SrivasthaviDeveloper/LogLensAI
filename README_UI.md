# VNSP Troubleshooter Dashboard

The Streamlit dashboard in `ui.py` calls the existing local FastAPI backend. It displays only the fabricated scenarios and logs served by that API; it does not query AWS or other infrastructure.

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
