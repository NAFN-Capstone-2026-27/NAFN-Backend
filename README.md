# NAFN Backend

AWS CDK (Python) infrastructure for NAFN.

## Stacks

- **FrontendStack** (`NAFNBackend/FrontendInfrastructure/frontend_stack.py`) hosts the static frontend:
  - A private S3 bucket named after the environment's domain (for example `dev.nafn.us`)
  - A CloudFront distribution that reads from the bucket through Origin Access Control, redirects HTTP to HTTPS, and has caching disabled
  - A Route 53 A record pointing the domain at the distribution, using an existing ACM certificate and hosted zone
  - A stack output, `S3FrontendBucket`, with the bucket name
- **NafnBackendStack** (`NAFNBackend/BackendInfrastructure/nafn_backend_stack.py`) is a placeholder and isn't deployed by `app.py` yet.

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
| `certificate_arn` | ARN of an existing ACM certificate for the domain   |
| `hosted_zone_id`  | ID of the existing Route 53 hosted zone             |
| `hosted_zone_name`| Name of the hosted zone (for example `nafn.us`)     |
| `domain_name`     | Domain the frontend is served from                  |
| `project_name`    | Used as the CloudFormation stack name               |

The only environment defined right now is `dev`, which serves `dev.nafn.us`. To add one, copy the `dev` block under a new key and change the values.

## Deploying

Every CDK command needs an environment passed with `-c env=<name>`. Without it, `app.py` raises `Environment must be specified`; a name that isn't in `cdk.json` raises `Unknown environment`.

```
$ cdk synth -c env=dev     # generate the CloudFormation template
$ cdk diff -c env=dev      # compare against what's deployed
$ cdk deploy -c env=dev    # deploy
```

After the deploy, upload the built frontend to the bucket named in the `S3FrontendBucket` output, for example:

```
$ aws s3 sync ./dist s3://dev.nafn.us --delete
```

Because CloudFront caching is disabled, changes show up right away without a cache invalidation.

## Other useful commands

- `cdk ls -c env=dev` lists the stacks in the app
- `cdk destroy -c env=dev` tears down the stack
