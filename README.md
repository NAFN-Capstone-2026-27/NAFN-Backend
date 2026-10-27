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
