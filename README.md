# NAFN Backend

AWS CDK (Python) infrastructure for NAFN.

## Stacks

- **FrontendStack** (`NAFNBackend/FrontendInfrastructure/frontend_stack.py`) hosts the static frontend:
  - A private S3 bucket named after the environment's domain (for example `dev.nafn.us`)
  - A CloudFront distribution that reads from the bucket through Origin Access Control, redirects HTTP to HTTPS, and has caching disabled
  - A Route 53 A record pointing the domain at the distribution, using an existing ACM certificate and hosted zone
  - A GitHub OIDC provider and a deployment role restricted to the frontend repository's `main` branch
  - Outputs `S3FrontendBucket` and `FrontendDeployRoleArn`
- **NafnBackendStack** (`NAFNBackend/BackendInfrastructure/nafn_backend_stack.py`) stores organizations and locations/services:
  - A partners DynamoDB table keyed by UUID string `partnerId`; optional EINs are duplicate-checked in application code
  - A resources DynamoDB table keyed by UUID string `resourceId`, optionally linked to a partner
  - Lambda-backed `POST /partners`, `GET /partners`, `POST /resources`, and `GET /resources`
  - Outputs `ApiUrl`, `PartnersTableName`, and `ResourcesTableName`
  - The old empty EIN-keyed table is temporarily defined with `DESTROY` policy to prepare its removal; no handler uses it

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

Per-environment settings live under `context.environments` in `cdk.json`. The frontend-specific settings below are required only when frontend creation is enabled:

| Key               | Description                                         |
| ----------------- | --------------------------------------------------- |
| `account`         | AWS account ID to deploy into                       |
| `region`          | AWS region to deploy into                           |
| `certificate_arn` | ARN of an existing ACM certificate for the domain   |
| `hosted_zone_id`  | ID of the existing Route 53 hosted zone             |
| `hosted_zone_name`| Name of the hosted zone (for example `nafn.us`)     |
| `domain_name`     | Domain the frontend is served from                  |
| `project_name`    | Used as the CloudFormation stack name               |
| `github_oidc_subject` | Exact GitHub OIDC subject allowed to assume the frontend deployment role |

`dev` targets account `463735866782` in `us-east-1`. The frontend call is
currently commented out in `app.py` for backend-only migration deployments.

`prod` targets account `892077328991` in `us-east-1`. Before any prod synthesis,
diff or deployment, manually comment out the complete `FrontendStack(...)` call
in `app.py`. Leave `NafnBackendStack(...)` enabled. Production frontend settings
are not configured yet; its temporary `domain_name` is `PLACEHOLDER`. Restore the
frontend block before working on the dev frontend. Backend-only dev deployments
can leave it commented out. There is no automatic frontend condition.

The optional `cors_allow_origins` setting overrides backend CORS origins. Dev
uses `["*"]`; prod preserves its deployed origins (`https://PLACEHOLDER`,
`https://nafn.us`, and `https://dev.nafn.us`). Production needs no frontend
certificate, hosted-zone or GitHub settings while the frontend call is commented.

Environment configuration does not authenticate to an account: use credentials
for the selected target and inspect its backend diff before deployment. The old table's DESTROY policy has been
deployed in both environments; its construct is now removed for the cleanup deployment. Automated writable smoke tests still refuse production.

## GitHub Actions authentication

The deployment role allows listing the website bucket and uploading/deleting its
objects. This stack creates the GitHub OIDC provider.

After deployment, set these variables in the frontend GitHub repository:

| CDK output | GitHub variable |
| --- | --- |
| `FrontendDeployRoleArn` | `AWS_ROLE_ARN` |
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
$ aws s3 sync ./dist s3://dev.nafn.us --delete
```

Because CloudFront caching is disabled, changes show up right away without a cache invalidation.

## Database rollout

Run local checks first; these do not call AWS:

```bash
python -m unittest discover -s tests -v
cdk synth NAFN-Backend -c env=dev
cdk diff NAFN-Backend -c env=dev --profile default
```

The old partners table is empty and disposable. The new tables have generated
physical names; IDs and timestamps are generated by the handlers. No third table
or secondary index is added. No approval workflow is implemented.

Deploy only the backend while forms are paused:

```bash
cdk deploy NAFN-Backend -c env=dev --profile default
```

The initial migration deployed the new tables/routes and handlers, and set the old
table's deletion policy to DESTROY in both dev and prod. The old `NafnPartnersTable`
construct is now removed. This cleanup deployment deletes that old empty table;
the new tables and existing API remain. Apply this cleanup to each environment.

Do not deploy production automatically. Recheck account, region and actual table
identities before any deletion. Once new records exist, reverting to old handlers
is not a data rollback and would hide the new data.

## Backend smoke tests (writes to AWS)

After the new backend is deployed:

```bash
python post_deployment_tests.py --env dev --profile default
```

The script creates uniquely named test partners with and without EINs, checks
normalized duplicate EIN and validation errors, creates linked/independent
resources, checks numeric coordinates and listings, then conditionally deletes
its records by `partnerId`/`resourceId`. Resources are cleaned up before partners.
It exits nonzero on failed checks or incomplete cleanup. A connection failure
that loses a create response may leave a uniquely named test record; inspect the
`Post-Deployment Test <run UUID>` records if cleanup cannot identify a returned ID.

The test only allows dev/staging, checks the AWS account and region, rejects a
stack shared with any disallowed environment, and reads both table names and the
API URL from stack outputs. The selected identity needs CloudFormation/STS read
access and DeleteItem permission on the test tables; public Lambda roles do not
receive delete permission. Keep those guards when adding environments.

EIN checks are intentionally scan-then-insert for this demo, so concurrent requests
can still create duplicates. Coordinates are supplied explicitly; address
geocoding, geographic indexes, demo seed imports and frontend forms are separate
steps. See [API.md](API.md) for the record shapes and endpoint examples.

## Other useful commands

- `cdk ls -c env=dev` lists the stacks in the app
- `cdk destroy -c env=dev` tears down the stack
