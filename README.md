# NAFN Backend

AWS CDK infrastructure and API for NAFN.

## Stacks

- **NAFN-Frontend:** Hosts the static frontend using S3, CloudFront, and Route 53, with a GitHub deployment role.
- **NAFN-Backend:** Stores partners and resources in DynamoDB and exposes GET/POST `/partners` and `/resources` through API Gateway and Lambda.

See [API.md](API.md) for API URLs, request fields, and frontend integration examples.

## Setup

Requires Python compatible with `pyproject.toml`, Node.js, the AWS CDK CLI, and AWS credentials for the target account. The account and region must be bootstrapped for CDK.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

## Environments and deployment

Settings live in `cdk.json`. Pass `-c env=dev` or `-c env=prod` to CDK commands.

Each entry under `context.environments` uses these fields:

| Field | Purpose |
| --- | --- |
| `account` | AWS account ID for both stacks |
| `region` | AWS region for both stacks |
| `project_name` | Prefix for stack names: `<project_name>-Frontend` and `<project_name>-Backend` |
| `domain_name` | Frontend domain and S3 bucket name |
| `certificate_arn` | Existing ACM certificate for the frontend domain; CloudFront requires it in `us-east-1` |
| `hosted_zone_id` | ID of the existing Route 53 hosted zone used for frontend DNS records |
| `hosted_zone_name` | Name of that hosted zone, such as `nafn.us` |
| `github_oidc_subject` | Exact GitHub OIDC subject allowed to assume the frontend deployment role |
| `cors_allow_origins` | Optional list of browser CORS origins; defaults to `["*"]` (any origin), as configured for both dev and prod. |

The certificate, hosted zone, and GitHub OIDC fields configure the frontend.
`app.py` currently instantiates both stacks, so those fields are required even
when selecting only the backend stack for deployment, unless the frontend stack
call is temporarily disabled as described below.

- **Dev:** Account `463735866782`, region `us-east-1`. Both stacks are enabled.
- **Prod:** Account `892077328991`, region `us-east-1`. Frontend settings are not configured yet; temporarily comment out `FrontendStack(...)` in `app.py` before running CDK for prod, then restore it afterward.

Inspect the diff before deploying the stack you changed:

```bash
cdk diff <stack-name> -c env=dev
cdk deploy <stack-name> -c env=dev
```

## Tests

```bash
python -m unittest discover -s tests
```
