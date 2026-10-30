import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Environment,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront,
    aws_cloudfront_origins,
    aws_iam as iam,
    aws_route53,
    aws_route53_targets,
    aws_s3,
)
from constructs import Construct


class FrontendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        cert_arn: str,
        hosted_zone_id: str,
        hosted_zone_name: str,
        domain_name: str,
        env: Environment,
        *,
        github_oidc_subject: str,
        enable_previews: bool = False,
        github_preview_oidc_subject: str | None = None,
    ) -> None:
        super().__init__(scope, construct_id, env=env)

        if enable_previews and not github_preview_oidc_subject:
            raise ValueError("Enabled previews require github_preview_oidc_subject")

        # Kinda surprised this needs both a hosted_zone_id and hosted_zone_name
        # I would have imagined hosted_zone_id is globally unique like s3
        hosted_zone = aws_route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=hosted_zone_id,
            zone_name=hosted_zone_name,
        )

        cert = acm.Certificate.from_certificate_arn(
            self, "Certificate", certificate_arn=cert_arn
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
            auto_delete_objects=True,
        )

        if enable_previews:
            # Expire preview files 30 days after their last upload.
            s3_bucket.add_lifecycle_rule(
                id="ExpirePreviewsAfter30Days",
                expiration=Duration.days(30),
                prefix="previews/",
            )

        # Sign requests that go to the bucket with AWS Signature Version 4
        s3_oac = aws_cloudfront.S3OriginAccessControl(
            self, "Frontend-S3-OAC", signing=aws_cloudfront.Signing.SIGV4_ALWAYS
        )

        function_associations = []
        domain_names = (
            [domain_name, f"*.{domain_name}"] if enable_previews else [domain_name]
        )
        if enable_previews:
            routing_code = Path(__file__).with_name("frontend_router.js").read_text()
            routing_code = routing_code.replace(
                "__ROUTING_CONFIG__",
                json.dumps(
                    {
                        "domainName": domain_name,
                    }
                ),
            )
            frontend_router = aws_cloudfront.Function(
                self,
                "FrontendRouter",
                code=aws_cloudfront.FunctionCode.from_inline(routing_code),
                runtime=aws_cloudfront.FunctionRuntime.JS_2_0,
                comment="Rewrite SPA routes and route PR preview subdomains",
            )

            function_associations.append(
                aws_cloudfront.FunctionAssociation(
                    function=frontend_router,
                    event_type=aws_cloudfront.FunctionEventType.VIEWER_REQUEST,
                )
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
                function_associations=function_associations,
            ),
            domain_names=domain_names,
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

        if enable_previews:
            aws_route53.ARecord(
                self,
                "PreviewARecord",
                zone=hosted_zone,
                record_name=f"*.{domain_name}.",
                target=aws_route53.RecordTarget.from_alias(
                    aws_route53_targets.CloudFrontTarget(distribution)
                ),
            )

            aws_route53.AaaaRecord(
                self,
                "PreviewAaaaRecord",
                zone=hosted_zone,
                record_name=f"*.{domain_name}.",
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
        if enable_previews:
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
            preview_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:ListBucket"],
                    resources=[s3_bucket.bucket_arn],
                    conditions={"StringLike": {"s3:prefix": "previews/*"}},
                )
            )
            preview_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:PutObject"],
                    resources=[s3_bucket.arn_for_objects("previews/*")],
                )
            )
            CfnOutput(self, "FrontendPreviewDeployRoleArn", value=preview_role.role_arn)

        CfnOutput(self, "S3FrontendBucket", value=s3_bucket.bucket_name)
        CfnOutput(self, "FrontendDeployRoleArn", value=deploy_role.role_arn)
