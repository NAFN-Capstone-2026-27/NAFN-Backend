import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Environment,
    RemovalPolicy,
    Stack,
    aws_cloudfront,
    aws_cloudfront_origins,
    aws_iam as iam,
    aws_route53,
    aws_route53_targets,
    aws_s3,
)
from aws_cdk import (
    aws_certificatemanager as acm,
)
from constructs import Construct


class FrontendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        hosted_zone_id: str,
        hosted_zone_name: str,
        domain_name: str,
        env: Environment,
        *,
        preview_domain_name: str,
        github_oidc_subject: str,
        github_preview_oidc_subject: str,
    ) -> None:
        super().__init__(scope, construct_id, env=env)

        # Kinda surprised this needs both a hosted_zone_id and hosted_zone_name
        # I would have imagined hosted_zone_id is globally unique like s3
        hosted_zone = aws_route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=hosted_zone_id,
            zone_name=hosted_zone_name,
        )

        cert = acm.Certificate(
            self,
            "FrontendCertificate",
            domain_name=domain_name,
            subject_alternative_names=[f"*.{preview_domain_name}"],
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        # We wil be using CloudFront origin access control to access the bucket
        # Therefore, we do not need public access
        s3_bucket = aws_s3.Bucket(
            self,
            "S3-Bucket",
            bucket_name=domain_name,
            block_public_access=aws_s3.BlockPublicAccess.BLOCK_ALL,
            versioned=False,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # Sign requests that go to the bucket with AWS Signature Version 4
        s3_oac = aws_cloudfront.S3OriginAccessControl(
            self, "Frontend-S3-OAC", signing=aws_cloudfront.Signing.SIGV4_ALWAYS
        )

        routing_code = Path(__file__).with_name("preview_router.js").read_text()
        routing_code = routing_code.replace(
            "__ROUTING_CONFIG__",
            json.dumps({
                "domainName": domain_name,
                "previewDomainName": preview_domain_name,
            }),
        )
        preview_router = aws_cloudfront.Function(
            self,
            "PreviewRouter",
            code=aws_cloudfront.FunctionCode.from_inline(routing_code),
            runtime=aws_cloudfront.FunctionRuntime.JS_2_0,
            comment="Route preview subdomains to their PR folders",
        )

        distribution = aws_cloudfront.Distribution(
            self,
            "CF-Distribution",
            default_behavior=aws_cloudfront.BehaviorOptions(
                origin=aws_cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                    s3_bucket,
                    origin_access_control=s3_oac,
                ),
                viewer_protocol_policy=aws_cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=aws_cloudfront.CachePolicy.CACHING_DISABLED,
                function_associations=[
                    aws_cloudfront.FunctionAssociation(
                        function=preview_router,
                        event_type=aws_cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            domain_names=[domain_name, f"*.{preview_domain_name}"],
            certificate=cert,
            default_root_object="index.html",
        )

        aws_route53.ARecord(
            self,
            "CFARecord",
            zone=hosted_zone,
            record_name=f"{domain_name}.",
            target=aws_route53.RecordTarget.from_alias(
                aws_route53_targets.CloudFrontTarget(distribution)
            ),
        )

        aws_route53.AaaaRecord(
            self,
            "CFAaaaRecord",
            zone=hosted_zone,
            record_name=f"{domain_name}.",
            target=aws_route53.RecordTarget.from_alias(
                aws_route53_targets.CloudFrontTarget(distribution)
            ),
        )

        aws_route53.ARecord(
            self,
            "PreviewARecord",
            zone=hosted_zone,
            record_name=f"*.{preview_domain_name}.",
            target=aws_route53.RecordTarget.from_alias(
                aws_route53_targets.CloudFrontTarget(distribution)
            ),
        )

        aws_route53.AaaaRecord(
            self,
            "PreviewAaaaRecord",
            zone=hosted_zone,
            record_name=f"*.{preview_domain_name}.",
            target=aws_route53.RecordTarget.from_alias(
                aws_route53_targets.CloudFrontTarget(distribution)
            ),
        )

        github_provider = iam.CfnOIDCProvider(
            self,
            "GitHubOidcProvider",
            url="https://token.actions.githubusercontent.com",
            client_id_list=["sts.amazonaws.com"],
        )

        deploy_role = iam.Role(
            self,
            "FrontendDeployRole",
            description="Deploy the NAFN frontend from GitHub Actions",
            assumed_by=iam.FederatedPrincipal(
                federated=github_provider.attr_arn,
                assume_role_action="sts:AssumeRoleWithWebIdentity",
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": github_oidc_subject,
                    }
                },
            ),
        )
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[s3_bucket.bucket_arn],
            )
        )
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:DeleteObject"],
                resources=[s3_bucket.arn_for_objects("*")],
            )
        )
        preview_role = iam.Role(
            self,
            "FrontendPreviewDeployRole",
            description="Deploy frontend PR previews from GitHub Actions",
            assumed_by=iam.FederatedPrincipal(
                federated=github_provider.attr_arn,
                assume_role_action="sts:AssumeRoleWithWebIdentity",
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": github_preview_oidc_subject,
                    }
                },
            ),
        )
        cleanup_role = iam.Role(
            self,
            "FrontendPreviewCleanupRole",
            description="Remove closed PR previews using trusted main workflow code",
            assumed_by=iam.FederatedPrincipal(
                federated=github_provider.attr_arn,
                assume_role_action="sts:AssumeRoleWithWebIdentity",
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": github_oidc_subject,
                    }
                },
            ),
        )
        for role in (preview_role, cleanup_role):
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:ListBucket"],
                    resources=[s3_bucket.bucket_arn],
                    conditions={"StringLike": {"s3:prefix": "previews/*"}},
                )
            )
        preview_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:DeleteObject"],
                resources=[s3_bucket.arn_for_objects("previews/*")],
            )
        )
        cleanup_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:DeleteObject"],
                resources=[s3_bucket.arn_for_objects("previews/*")],
            )
        )
        CfnOutput(self, "S3FrontendBucket", value=s3_bucket.bucket_name)
        CfnOutput(self, "FrontendDeployRoleArn", value=deploy_role.role_arn)
        CfnOutput(self, "FrontendPreviewDeployRoleArn", value=preview_role.role_arn)
        CfnOutput(self, "FrontendPreviewCleanupRoleArn", value=cleanup_role.role_arn)
