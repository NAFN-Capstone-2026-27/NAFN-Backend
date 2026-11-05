# NAFN Backend

AWS CDK (Python) infrastructure for NAFN.

## Stacks

- **FrontendStack** (`NAFNBackend/FrontendInfrastructure/frontend_stack.py`) hosts the static frontend:
  - A private S3 bucket named after the environment's domain (for example `dev.nafn.us`)
  - A CloudFront distribution that reads from the bucket through Origin Access Control and redirects HTTP to HTTPS. Only `/assets/*` is cached in staging and prod.
  - Route 53 A/AAAA alias records for the website and, in dev, `*.<domain_name>`
  - An existing ACM certificate from the environment's `certificate_arn`; the dev certificate also covers preview subdomains
  - A CloudFront Function for React deep links in every environment and PR subdomain routing in dev
  - Outputs `S3FrontendBucket`
- **NafnBackendStack** (`NAFNBackend/BackendInfrastructure/nafn_backend_stack.py`) stores partner organizations:
  - A DynamoDB table keyed by `ein`
  - An HTTP API with `POST /partners`, backed by a Lambda function (`NAFNBackend/BackendInfrastructure/lambda/`) that validates and stores a partner
  - `GET /partners`, backed by a Lambda function that lists every partner
  - Stack outputs `ApiUrl` and `PartnersTableName`
- **GitHubOidcStack** (`NAFNBackend/DeploymentInfrastructure/github_oidc_stack.py`) lets GitHub Actions deploy to the account:
  - The account's GitHub OIDC provider (an account can only have one, so every deploy role shares it)
  - A staging and prod frontend deployment role, restricted to `frontend_github_oidc_subject`, that can upload to and delete from the website bucket
  - A frontend preview role, restricted to `frontend_preview_github_oidc_subject`, that can upload to only `previews/` in the website bucket. Only created for environments that set `frontend_preview_github_oidc_subject` (currently just `dev`)
  - A CDK deployment role restricted to `cdk_deploy_github_oidc_subject` and the target account's standard CDK deploy, file publishing, and lookup roles
  - Outputs `FrontendDeployRoleArn`, `FrontendPreviewDeployRoleArn`, and `CdkDeployRoleArn` when the corresponding roles exist

The API's routes, request and response formats, and restrictions are documented in [API.md](API.md).

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
| `enable_previews` | Optional. Serves frontend PR previews from `pr-<number>.<domain_name>` |
| `frontend_github_oidc_subject` | Optional. Exact GitHub OIDC subject allowed to assume the frontend deployment role |
| `frontend_preview_github_oidc_subject` | Exact frontend GitHub environment subject allowed to assume the preview deployment role. Required when `enable_previews` is set |
| `cdk_deploy_github_oidc_subject` | Optional. Exact GitHub OIDC subject allowed to assume the CDK deployment role |

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
environments. They deploy `NAFN-Backend` and `NAFN-Frontend`. The OIDC stack is
managed separately from application deployments.

| Environment variable | Value |
| --- | --- |
| `AWS_ROLE_ARN` | `CdkDeployRoleArn` from the target account's OIDC stack |
| `DEPLOY_MAINTAINERS` | Space- or comma-separated GitHub logins in dev and prod |

The workflows read the AWS account and region from `cdk.json` and verify the
authenticated account before deploying.

- **Dev** — **Deploy backend PR to dev** accepts an open same-repo PR number or
  `main` as a restore source. The maintainer-only workflow deploys the selected
  revision and smoke-tests the shared API.
- **Staging** — Pushes to `main` deploy both stacks and run the staging API smoke
  test. A successful run summary includes the run ID for production promotion.
- **Prod** — **Promote backend to production** is a maintainer-only workflow
  that accepts a successful staging run ID. It synthesizes that revision for
  prod, logs a CDK diff, deploys both stacks, and runs a read-only API check.

## Backend smoke tests

`post_deployment_tests.py` creates a dummy partner with an EIN starting with
`00`, checks duplicate and validation errors, checks that `GET /partners`
lists it, and deletes the dummy record. It exits nonzero if a check fails.

Because it creates and deletes records, the script only runs against environments listed in `WRITABLE_ENVIRONMENTS` in `post_deployment_tests.py` (currently `dev` and `staging`). It refuses to run if your AWS credentials are for a different account than the environment's, or if the environment deploys to the same stack as one that isn't allowed. Its cleanup only deletes an item that is still the dummy partner.

## Local checks

- `node --test tests/frontend_router.test.cjs` tests SPA and preview routing locally
- `python -m unittest tests.test_preview_permissions` tests deployment permission boundaries
- `cdk synth -c env=dev` generates the dev CloudFormation templates
