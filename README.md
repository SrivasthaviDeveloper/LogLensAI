# VNSP Incident Triage

A small Python/Streamlit starter for routing VNSP incident investigations to configured services and searching their AWS CloudWatch log groups. The service catalog is intentionally a template: populate it with the real VNSP service names, deployment environments, AWS regions, and log group names before using it for operational investigation.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

On macOS/Linux, activate with `source .venv/bin/activate`.

## AWS access

The app uses `boto3.Session()` and the AWS SDK default credential chain. Configure an AWS profile/SSO session or a role for the identity that runs Streamlit. The identity needs read-only CloudWatch Logs permissions for the configured groups in each region (at minimum `logs:FilterLogEvents`; depending on organization policy, `logs:DescribeLogGroups` may also be useful). Select credentials outside the application; do not add access keys to the source or service catalog.

For a named profile, set `AWS_PROFILE` before launching Streamlit. For example:

```powershell
$env:AWS_PROFILE = "vnsp-readonly"
streamlit run app.py
```

## Configure service routing

Edit `config/services.json`. Each service supports:

- `name`: actual microservice or system name shown to the investigator
- `role`: its role in the request path
- `environment`: deployment environment / account label
- `regions`: AWS regions in which it runs
- `log_groups`: CloudWatch log group names to search in each listed region

Add multiple `applications` if the caller application is distinct from VNSP. Keep the catalog free of passwords, tokens, and other secrets. Set `VNSP_CONFIG` to use a different catalog path.

## Current behavior and limits

- The form accepts a symptom and optional request/correlation ID, displays configured investigation targets, and gives general checks for credential, timeout, and server errors.
- **Check and validate** searches configured CloudWatch log groups over a bounded lookback window. It searches by correlation ID when provided, otherwise by the entered error text.
- Searches are read-only. This starter does not SSH to servers, query application databases, or modify AWS resources.
- Configure actual service mappings, IAM access, and log retention before operational use. Correlation IDs are generally a more reliable search term than a broad error description.
