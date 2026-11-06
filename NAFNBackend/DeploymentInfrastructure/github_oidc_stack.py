from aws_cdk import (
    CfnOutput,
    Environment,
    RemovalPolicy,
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
        environment_name: str,
        frontend_bucket_name: str,
        frontend_github_oidc_subject: str | None = None,
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
        self.github_provider.apply_removal_policy(RemovalPolicy.RETAIN)

        # The bucket is named after the domain, so its ARN is known without
        # referencing FrontendStack.
        frontend_bucket_arn = f"arn:aws:s3:::{frontend_bucket_name}"
        if frontend_github_oidc_subject:
            frontend_deploy_role = iam.Role(
                self,
                "FrontendDeployRole",
                description="Deploy the NAFN frontend from GitHub Actions",
                assumed_by=self._github_principal(frontend_github_oidc_subject),
            )
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
            CfnOutput(
                self, "FrontendDeployRoleArn", value=frontend_deploy_role.role_arn
            )

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

        if cdk_deploy_github_oidc_subject:
            cdk_deploy_role = iam.Role(
                self,
                "CdkDeployRole",
                description=f"Deploy NAFN {environment_name} from GitHub Actions",
                assumed_by=self._github_principal(cdk_deploy_github_oidc_subject),
            )

            cdk_deploy_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[
                        f"arn:{self.partition}:iam::{self.account}:role/"
                        f"cdk-{CDK_BOOTSTRAP_QUALIFIER}-{purpose}-role-"
                        f"{self.account}-{self.region}"
                        for purpose in ("deploy", "file-publishing", "lookup")
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
