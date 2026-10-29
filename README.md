# NAFN Backend

AWS CDK (Python) infrastructure for NAFN.

## Stacks

- **FrontendStack** (`NAFNBackend/FrontendInfrastructure/frontend_stack.py`) hosts the static frontend:
  - A private S3 bucket named after the environment's domain (for example `dev.nafn.us`)
  - A CloudFront distribution that reads from the bucket through Origin Access Control, redirects HTTP to HTTPS, and has caching disabled
  - Route 53 A/AAAA alias records for the website and `*.<domain_name>`, pointing to the distribution
  - An existing ACM certificate from the environment's `certificate_arn`; the dev certificate also covers preview subdomains
  - A CloudFront Function mapping `pr-<number>.<domain_name>` to `previews/pr-<number>/`, with React fallback for extensionless page paths
  - A GitHub OIDC provider and a deployment role restricted to the frontend repository's `main` branch
  - Outputs `S3FrontendBucket` and `FrontendDeployRoleArn`
- **NafnBackendStack** (`NAFNBackend/BackendInfrastructure/nafn_backend_stack.py`) stores partner organizations:
  - A DynamoDB table keyed by `ein`
  - An HTTP API with `POST /partners`, backed by a Lambda function (`NAFNBackend/BackendInfrastructure/lambda/`) that validates and stores a partner
  - `GET /partners`, backed by a Lambda function that lists every partner
  - Stack outputs `ApiUrl` and `PartnersTableName`

The API's routes, request and response formats, and restrictions are documented in [API.md](API.md).

## Prerequisites

- Python 3.12 or newer
- Node.js and the AWS CDK CLI (`npm install -g aws-cdk`)
- AWS credentials for the target account (for example via `aws configure` or `aws sso login`)
- The target account/region bootstrapped for CDK (only needed once per account/region):

  ```
  $ cdk bootstrap aws://463735866782/us-east-1
  ```

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
| `github_oidc_subject` | Exact GitHub OIDC subject allowed to assume the frontend deployment role |
| `github_preview_oidc_subject` | Exact PR OIDC subject allowed to assume the preview deployment role |

The only environment defined right now is `dev`, which serves `dev.nafn.us`. To add one, copy the `dev` block under a new key and change the values.

CloudFront uses the existing certificate configured for each environment. Preview requests
use their PR's files and `index.html`; asset requests stay within that PR's prefix.
The same routing function serves the root `index.html` for extensionless React
routes on the dev domain and CloudFront distribution hostname, preserving asset paths.

Preview URLs use `pr-<number>.dev.nafn.us`.

## GitHub Actions authentication

The main deployment role can list the website bucket and upload/delete its objects.
The preview role can list/upload/delete only under `previews/` and is shared by
preview deployment and cleanup. Both roles use the same GitHub OIDC provider.

The preview role trusts both PR and `main` subjects. Cleanup uses `main`, for a `pull_request_target` workflow handling
closed PRs from the default branch (`main`). That workflow must never check out or
execute PR code. PR preview deployment uses `pull_request` without a GitHub
environment; restrict it to same-repository PRs in the workflow. Preview access is
shared across PR prefixes; the workflow must select the correct PR folder.

When previews are enabled, S3 expires files under `previews/` 30 days after
their last upload, regardless of whether the PR is still open. Expiration is
asynchronous and does not affect the main website files.

After deployment, set these variables in the frontend GitHub repository:

| CDK output | GitHub variable |
| --- | --- |
| `FrontendDeployRoleArn` | `AWS_MAIN_ROLE_ARN` |
| `FrontendPreviewDeployRoleArn` | `AWS_PREVIEW_ROLE_ARN` |
| `S3FrontendBucket` | `S3_BUCKET` |

Also set `AWS_REGION` to the environment's region (`us-east-1` for `dev`).

## Deploying

Every CDK command needs an environment passed with `-c env=<name>`. Without it, `app.py` raises `Environment must be specified`; a name that isn't in `cdk.json` raises `Unknown environment`.

```
$ cdk synth -c env=dev     # generate the CloudFormation template
$ cdk diff -c env=dev      # compare against what's deployed
$ cdk deploy --all -c env=dev    # deploy all stacks (NAFN-Backend, NAFN-Frontend)
```

After the deploy, upload the built frontend to the bucket named in the `S3FrontendBucket` output, for example:

```
$ aws s3 sync ./dist s3://dev.nafn.us
```

Because CloudFront caching is disabled, changes show up right away without a cache invalidation.

After deploying the backend, smoke-test it:

```
$ python post_deployment_tests.py --env dev
```

It creates a dummy partner (with an EIN starting with `00`, which the IRS never issues), checks the duplicate and validation errors, checks that `GET /partners` lists it, then deletes the dummy partner. It exits non-zero if any check fails.

Because it creates and deletes records, the script only runs against environments listed in `WRITABLE_ENVIRONMENTS` in `post_deployment_tests.py` (currently `dev` and `staging`). It refuses to run if your AWS credentials are for a different account than the environment's, or if the environment deploys to the same stack as one that isn't allowed. Its cleanup only deletes an item that is still the dummy partner.

## Other useful commands

- `node --test tests/frontend_router.test.cjs` tests SPA and preview routing locally
- `python -m unittest tests.test_preview_permissions` tests deployment permission boundaries

- `cdk ls -c env=dev` lists the stacks in the app
- `cdk destroy -c env=dev` tears down the stack
