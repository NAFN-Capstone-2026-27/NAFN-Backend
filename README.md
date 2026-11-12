# NAFN Backend

AWS CDK (Python) infrastructure for NAFN.

## Stacks

- **FrontendStack** (`NAFNBackend/FrontendInfrastructure/frontend_stack.py`) hosts the static frontend:
  - A private S3 bucket named after the environment's domain (for example `dev.nafn.us`)
  - A CloudFront distribution that reads from the bucket through Origin Access Control and redirects HTTP to HTTPS. Only `/assets/*` is cached in staging and prod.
  - Route 53 A/AAAA alias records for the website and `*.<domain_name>` where previews are enabled
  - An existing ACM certificate from the environment's `certificate_arn`; the dev certificate also covers preview subdomains
  - A CloudFront Function for React deep links in every environment and PR subdomain routing where previews are enabled
  - Outputs `S3FrontendBucket`
- **DataStack** (`NAFNBackend/DataInfrastructure/data_stack.py`) owns the environment's DynamoDB tables:
  - Tables keyed by generated `partnerId` and `resourceId` UUIDs, with on-demand billing, point-in-time recovery, and retention on removal
  - Single-region tables; multi-region replication and reservations are not enabled yet
  - Outputs `PartnersTableName` and `ResourcesTableName`
- **ApiStack** (`NAFNBackend/BackendInfrastructure/api_stack.py`, deployed as `NAFN-Api`) receives table references from `DataStack`:
  - Lambda environment variables and table-scoped permissions reference the data stack's tables
  - An HTTP API with GET and POST routes for `/partners` and `/resources`, backed by Lambda functions
  - A custom API domain at `api.<domain_name>` (for example `api.dev.nafn.us`), with a DNS-validated regional ACM certificate and Route 53 alias record
  - A private RDS PostgreSQL instance
  - `ApiUrl` returns the custom HTTPS API address; API outputs include `ApiUrl`, `PartnersTableName`, and `ResourcesTableName`
- **GitHubOidcStack** (`NAFNBackend/DeploymentInfrastructure/github_oidc_stack.py`) lets GitHub Actions deploy to the account:
  - The account's GitHub OIDC provider (an account can only have one, so every deploy role shares it)
  - A staging and prod frontend deployment role, restricted to `frontend_github_oidc_subject`, that can upload to and delete from the website bucket
  - A frontend preview role, restricted to `frontend_preview_github_oidc_subject`, that can upload only under `previews/` in the website bucket
  - A CDK deployment role restricted to `cdk_deploy_github_oidc_subject` and the target account's standard CDK deploy, file publishing, and lookup roles
  - Outputs `FrontendDeployRoleArn`, `FrontendPreviewDeployRoleArn`, and `CdkDeployRoleArn` when the corresponding roles exist

The API's routes, request and response formats, and restrictions are documented in [API.md](API.md).

### Existing deployments: stack migration

The DynamoDB tables now belong to `NAFN-Data` instead of `NAFN-Backend`.
The remaining backend stack is now named `NAFN-Api`. CloudFormation treats this
name as a different stack, so its existing resources must also be migrated;
a normal deploy does not rename or remove `NAFN-Backend`.
Do not apply this split to an existing environment with a normal `cdk deploy`:
that would create new empty tables, redirect the API to them, and retain the old
ones. First complete a supported resource refactor or a retain/import migration,
verifying that both tables keep their existing physical IDs and data. Review the
migration and CDK diff for each environment before resuming normal deployments.
Fresh environments can deploy all stacks normally; cross-stack references make
`NAFN-Api` depend on `NAFN-Data`. API table-name outputs remain available
for existing consumers.

## Prerequisites

- Python 3.12 or newer
- Node.js and the AWS CDK CLI (`npm install -g aws-cdk`)
- Deployments use AWS credentials for the target account and the standard CDK bootstrap in that account and region.

## Setup

Create and activate a virtualenv:

```
$ python3 -m venv .venv
$ source .venv/bin/activate
```

Install the project and its dependencies (declared in `pyproject.toml`):

```
$ pip install .
```

To add dependencies, add them to the `dependencies` list in `pyproject.toml` and run `pip install .` again.

## Linting and formatting

Install the development tools into your activated virtualenv:

```bash
pip install -e '.[dev]'
```

Run the same checks as CI from the repository root:

```bash
ruff check .
ruff format --check .
```

To apply automatic fixes and formatting locally:

```bash
ruff check --fix .
ruff format .
```

Shared settings and the pinned Ruff version live in `pyproject.toml`.

## Environments

Per-environment settings live under `context.environments` in `cdk.json`. Each environment needs:

| Key               | Description                                         |
| ----------------- | --------------------------------------------------- |
| `account`         | AWS account ID to deploy into                       |
| `region`          | AWS region to deploy into                           |
| `certificate_arn` | ARN of the existing ACM certificate in `us-east-1` |
| `hosted_zone_id`  | ID of the existing Route 53 hosted zone             |
| `hosted_zone_name`| Name of the hosted zone (for example `nafn.us`)     |
| `domain_name`     | Domain the frontend is served from                  |
| `project_name`    | Used as the CloudFormation stack name               |
| `frontend_github_oidc_subject` | Optional. Exact GitHub OIDC subject allowed to assume the frontend deployment role |
| `frontend_preview_github_oidc_subject` | Optional. Enables frontend previews and sets the exact GitHub OIDC subject allowed to assume the preview deployment role. Configured in dev only |
| `cdk_deploy_github_oidc_subject` | Optional. Exact GitHub OIDC subject allowed to assume the CDK deployment role |
| `db_instance_class`, `db_instance_size` | CDK EC2 instance class and size for RDS |
| `cors_allow_origins` | Optional. List of browser origins allowed by the API. Defaults to `["*"]` |

The configured environments are `dev.nafn.us`, `staging.nafn.us`, and `nafn.us` for dev, staging, and prod.

CloudFront uses the existing certificate configured for each environment. Preview requests
use their PR's files and `index.html`; asset requests stay within that PR's prefix.
The same routing function serves the root `index.html` for extensionless React
routes on each site's domain and CloudFront distribution hostname, preserving asset paths.

Preview URLs use `pr-<number>.dev.nafn.us`.

## GitHub Actions authentication

The frontend deployment role can list the website bucket and upload/delete its objects.
The preview role can list/upload only under `previews/` and trusts the frontend
repo's `dev` GitHub environment. Both roles live in `NAFN-GitHubOidc` and use its
GitHub OIDC provider. The frontend workflow publishes same-repository PRs to
separate prefixes in dev.

When previews are enabled, S3 expires files under `previews/` 30 days after
their last upload, regardless of whether the PR is still open. Expiration is
asynchronous and does not affect the main website files.

The frontend workflow uses `FrontendPreviewDeployRoleArn` in dev and
`FrontendDeployRoleArn` in staging and prod. Its deployment targets correspond
to the environment's `S3FrontendBucket` and `ApiUrl` outputs. Backend workflows
use `CdkDeployRoleArn` and assume the standard CDK bootstrap roles.

## GitHub Actions deployments

The backend workflows run from `main` in the `dev`, `staging`, and `prod` GitHub
environments. Each workflow deploys all stacks in the selected revision.

| Environment variable | Value |
| --- | --- |
| `AWS_ROLE_ARN` | `CdkDeployRoleArn` from the target account's OIDC stack |

The workflows read the AWS account and region from `cdk.json` and verify the
authenticated account before deploying.

Before the first workflow run in each environment, use separate AWS credentials
to set up `NAFN-GitHubOidc` and configure `AWS_ROLE_ARN` from the stack output. If a later deployment breaks the role's trust or replaces the role, use separate credentials to restore
access and update `AWS_ROLE_ARN` if the ARN changed.

- **Dev** — **Deploy backend PR to dev** accepts an open same-repo PR number or
  `main` as a restore source. Users with repository write access can run it.
- **Staging** — Pushes to `main` deploy all stacks. A successful run summary
  includes the run ID for production promotion.
- **Prod** — Anyone with repository write access can run **Promote backend to
  production** with a successful staging run ID. It authenticates to prod, logs a
  CDK diff for all stacks in that revision, and deploys them.

## Manual backend smoke tests

`python post_deployment_tests.py --env dev` checks the DynamoDB-backed API.
It creates a temporary partner and a linked resource, checks validation and list
responses, and cleans up both records. It exits nonzero if a check fails.

Because it creates and deletes records, the script only runs against environments listed in `WRITABLE_ENVIRONMENTS` in `post_deployment_tests.py` (currently `dev` and `staging`). It refuses to run if your AWS credentials are for a different account than the environment's, or if the environment deploys to the same stack as one that isn't allowed.

## Local checks

- `node --test tests/frontend_router.test.cjs` tests SPA and preview routing locally
- `python -m unittest discover -s tests` runs the backend and deployment tests
- `cdk synth -c env=dev` generates the dev CloudFormation templates
