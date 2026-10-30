from aws_cdk import (
    CfnOutput,
    Environment,
    Stack,
    aws_iam as iam,
)
from constructs import Construct

# The qualifier `cdk bootstrap` uses unless it's given --qualifier.
CDK_BOOTSTRAP_QUALIFIER = "hnb659fds"


class GitHubOidcStack(Stack):
    """GitHub OIDC provider and the roles GitHub Actions workflows deploy with."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env: Environment,
        *,
        frontend_bucket_name: str,
        frontend_github_oidc_subject: str,
        frontend_preview_github_oidc_subject: str | None = None,
        cdk_deploy_github_oidc_subject: str | None = None,
    ) -> None:
        super().__init__(scope, construct_id, env=env)

        # An account can only have one provider per URL, so every role that
        # GitHub Actions assumes has to share this one.
        self.github_provider = iam.CfnOIDCProvider(
            self,
            "GitHubOidcProvider",
            url="https://token.actions.githubusercontent.com",
            client_id_list=["sts.amazonaws.com"],
        )

        frontend_deploy_role = iam.Role(
            self,
            "FrontendDeployRole",
            description="Deploy the NAFN frontend from GitHub Actions",
            assumed_by=self._github_principal(frontend_github_oidc_subject),
        )
        # The bucket is named after the domain, so its ARN is known without
        # referencing FrontendStack.
        frontend_bucket_arn = f"arn:aws:s3:::{frontend_bucket_name}"
        frontend_deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[frontend_bucket_arn],
            )
        )
        frontend_deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:DeleteObject"],
                resources=[f"{frontend_bucket_arn}/*"],
            )
        )
        CfnOutput(self, "FrontendDeployRoleArn", value=frontend_deploy_role.role_arn)

        # Only environments with frontend PR previews get the preview role.
        if frontend_preview_github_oidc_subject:
            preview_role = iam.Role(
                self,
                "FrontendPreviewDeployRole",
                description="Deploy frontend PR previews from GitHub Actions",
                assumed_by=self._github_principal(frontend_preview_github_oidc_subject),
            )
            preview_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:ListBucket"],
                    resources=[frontend_bucket_arn],
                    conditions={"StringLike": {"s3:prefix": "previews/*"}},
                )
            )
            preview_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:PutObject"],
                    resources=[f"{frontend_bucket_arn}/previews/*"],
                )
            )
            CfnOutput(self, "FrontendPreviewDeployRoleArn", value=preview_role.role_arn)

        # Only environments deployed from this repository's GitHub Actions
        # workflow get a role that can deploy the CDK app.
        if cdk_deploy_github_oidc_subject:
            cdk_deploy_role = iam.Role(
                self,
                "CdkDeployRole",
                description="Deploy every stack in the NAFN CDK app from GitHub Actions",
                assumed_by=self._github_principal(cdk_deploy_github_oidc_subject),
            )

            # `cdk deploy` does the actual work for every stack through the roles
            # `cdk bootstrap` created, so assuming them is enough to run
            # `cdk deploy --all`, including updates to this stack.
            cdk_deploy_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[
                        f"arn:aws:iam::{self.account}:role/cdk-{CDK_BOOTSTRAP_QUALIFIER}-*-{self.account}-{self.region}"
                    ],
                )
            )

            CfnOutput(self, "CdkDeployRoleArn", value=cdk_deploy_role.role_arn)

    def _github_principal(self, github_oidc_subject: str) -> iam.FederatedPrincipal:
        return iam.FederatedPrincipal(
            federated=self.github_provider.attr_arn,
            assume_role_action="sts:AssumeRoleWithWebIdentity",
            conditions={
                "StringEquals": {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    "token.actions.githubusercontent.com:sub": github_oidc_subject,
                }
            },
        )
