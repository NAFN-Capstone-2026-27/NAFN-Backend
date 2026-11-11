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
        environment_name: str,
        cert_arn: str,
        hosted_zone_id: str,
        hosted_zone_name: str,
        domain_name: str,
        env: Environment,
        *,
        enable_previews: bool = False,
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

        cert = acm.Certificate.from_certificate_arn(
            self, "Certificate", certificate_arn=cert_arn
        )

        is_prod = environment_name == "prod"
        s3_bucket = aws_s3.Bucket(
            self,
            "S3-Bucket",
            bucket_name=domain_name,
            block_public_access=aws_s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
            auto_delete_objects=not is_prod,
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

        domain_names = (
            [domain_name, f"*.{domain_name}"] if enable_previews else [domain_name]
        )
        routing_code = Path(__file__).with_name("frontend_router.js").read_text()
        routing_code = routing_code.replace(
            "__ROUTING_CONFIG__",
            json.dumps({"domainName": domain_name, "enablePreviews": enable_previews}),
        )
        frontend_router = aws_cloudfront.Function(
            self,
            "FrontendRouter",
            code=aws_cloudfront.FunctionCode.from_inline(routing_code),
            runtime=aws_cloudfront.FunctionRuntime.JS_2_0,
            comment="Rewrite SPA routes and route PR preview subdomains",
        )
        function_associations = [
            aws_cloudfront.FunctionAssociation(
                function=frontend_router,
                event_type=aws_cloudfront.FunctionEventType.VIEWER_REQUEST,
            )
        ]
        origin = aws_cloudfront_origins.S3BucketOrigin.with_origin_access_control(
            s3_bucket,
            origin_access_control=s3_oac,
        )
        asset_behaviors = {}
        if environment_name != "dev":
            asset_behaviors["/assets/*"] = aws_cloudfront.BehaviorOptions(
                origin=origin,
                viewer_protocol_policy=aws_cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=aws_cloudfront.CachePolicy.CACHING_OPTIMIZED,
                function_associations=function_associations,
            )

        distribution = aws_cloudfront.Distribution(
            self,
            "CF-Distribution",
            default_behavior=aws_cloudfront.BehaviorOptions(
                origin=origin,
                viewer_protocol_policy=aws_cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=aws_cloudfront.CachePolicy.CACHING_DISABLED,
                function_associations=function_associations,
            ),
            additional_behaviors=asset_behaviors,
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

        CfnOutput(self, "S3FrontendBucket", value=s3_bucket.bucket_name)
