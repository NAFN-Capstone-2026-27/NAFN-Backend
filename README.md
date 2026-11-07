# NAFN Backend

AWS CDK infrastructure and API for NAFN.

## Stacks

- **NAFN-Frontend:** Hosts the static frontend using S3, CloudFront, and Route 53, with a GitHub deployment role.
- **NAFN-Backend:** Stores partners and resources in DynamoDB and exposes GET/POST `/partners` and `/resources` through API Gateway and Lambda. It also provisions a private RDS PostgreSQL instance.

See [API.md](API.md) for API URLs, request fields, and frontend integration examples.

## Setup

Requires Python compatible with `pyproject.toml`, Node.js, the AWS CDK CLI, and AWS credentials for the target account. The account and region must be bootstrapped for CDK.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

## Environments and deployment

Settings live in `cdk.json`. Pass `-c env=dev`, `-c env=staging`, or `-c env=prod` to CDK commands.

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
| `enable_previews` | Enables PR preview hosting in dev |
| `github_preview_oidc_subject` | Exact GitHub OIDC subject for the preview deployment role when previews are enabled |
| `db_instance_class`, `db_instance_size` | CDK EC2 instance class and size for RDS |
| `cors_allow_origins` | Optional list of browser CORS origins; defaults to `["*"]` (any origin), as configured for both dev and prod. |

Both stacks are configured for dev, staging, and prod. Dev serves
`dev.nafn.us`, staging serves `staging.nafn.us`, and prod serves `nafn.us`.
PR previews use `pr-<number>.dev.nafn.us`. The preview role can upload
only under `previews/`, where S3 expires files 30 days after upload.

After deploying the frontend, set `AWS_MAIN_ROLE_ARN` and `S3_BUCKET` from
`FrontendDeployRoleArn` and `S3FrontendBucket`. For dev previews, set
`AWS_PREVIEW_ROLE_ARN` from `FrontendPreviewDeployRoleArn`. Set `AWS_REGION`
to the environment's region.

Inspect the diff before deploying the stack you changed:

```bash
cdk diff <stack-name> -c env=dev
cdk deploy <stack-name> -c env=dev
```

## Tests

```bash
python -m unittest discover -s tests
node --test tests/frontend_router.test.cjs
```

Install the pinned Ruff version with `pip install -e '.[dev]'`. Run
`ruff check .` and `ruff format --check .` to match CI.

After a backend deployment, run `python post_deployment_tests.py --env dev`
or use `--env staging`. The script creates and cleans up temporary partner
and resource records and restricts writes to dev and staging.
