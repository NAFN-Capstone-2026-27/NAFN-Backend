from aws_cdk import (
    CfnOutput,
    Environment,
    RemovalPolicy,
    Stack,
    aws_cloudfront,
    aws_cloudfront_origins,
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
        cert_arn: str,
        hosted_zone_id: str,
        hosted_zone_name: str,
        domain_name: str,
        env: Environment,
    ) -> None:
        super().__init__(scope, construct_id, env=env)

        cert = acm.Certificate.from_certificate_arn(
            self, "Certificate", certificate_arn=cert_arn
        )

        # Kinda surprised this needs both a hosted_zone_id and hosted_zone_name
        # I would have imagined hosted_zone_id is globally unique like s3
        hosted_zone = aws_route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=hosted_zone_id,
            zone_name=hosted_zone_name,
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
        )

        # Sign requests that go to the bucket with AWS Signature Version 4
        s3_oac = aws_cloudfront.S3OriginAccessControl(
            self, "Frontend-S3-OAC", signing=aws_cloudfront.Signing.SIGV4_ALWAYS
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
                # CACHING_OPTIMIZED still let's you set Cache-Control headers, but it has a minimum TTL of 1s so no-cache will not work
                cache_policy=aws_cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            domain_names=[domain_name],
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

        CfnOutput(self, "S3FrontendBucket", value=s3_bucket.bucket_name)