from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_certificatemanager as acm,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
)
from constructs import Construct

LAMBDA_DIR = Path(__file__).parent / "lambda"


class NafnBackendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_name: str,
        hosted_zone_id: str,
        hosted_zone_name: str,
        cors_allow_origins: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.partners_table = dynamodb.TableV2(
            self,
            "NafnPartnersTable",
            partition_key=dynamodb.Attribute(
                name="partnerId", type=dynamodb.AttributeType.STRING
            ),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.resources_table = dynamodb.TableV2(
            self,
            "NafnResourcesTable",
            partition_key=dynamodb.Attribute(
                name="resourceId", type=dynamodb.AttributeType.STRING
            ),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        # One item per claimed EIN, keyed by the normalized EIN. Creating a partner
        # with an EIN first claims it here with a conditional put; only the
        # request that owns the claim may then create the partner. This does not
        # use transactions, because the planned multi-region strongly consistent
        # (MRSC) global tables do not support them. Claims never expire, so a
        # creation that fails after claiming leaves the EIN reserved.
        # This table is still single-region; moving it to MRSC needs the separate
        # data stack and migration plan.
        self.partner_ein_reservations_table = dynamodb.TableV2(
            self,
            "NafnPartnerEinReservationsTable",
            partition_key=dynamodb.Attribute(
                name="ein", type=dynamodb.AttributeType.STRING
            ),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        lambda_code = lambda_.Code.from_asset(
            str(LAMBDA_DIR),
            exclude=["__pycache__", "**/__pycache__/**", "*.pyc", "**/*.pyc"],
        )

        self.create_partner_function = lambda_.Function(
            self,
            "NafnCreatePartnerFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="create_partner.handler",
            code=lambda_code,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "PARTNERS_TABLE_NAME": self.partners_table.table_name,
                "PARTNER_EIN_RESERVATIONS_TABLE_NAME": (
                    self.partner_ein_reservations_table.table_name
                ),
            },
            log_group=logs.LogGroup(
                self,
                "NafnCreatePartnerLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        self.partners_table.grant(
            self.create_partner_function,
            "dynamodb:PutItem",
            "dynamodb:GetItem",
            "dynamodb:Scan",
        )
        # PutItem claims an EIN; GetItem checks who owns a claim when a write's
        # outcome is uncertain or a retry finds the EIN already claimed.
        self.partner_ein_reservations_table.grant(
            self.create_partner_function, "dynamodb:PutItem", "dynamodb:GetItem"
        )

        self.api = apigw.HttpApi(
            self,
            "NafnApi",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=(
                    cors_allow_origins if cors_allow_origins is not None else ["*"]
                ),
                allow_methods=[apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.GET],
                allow_headers=["Content-Type", "Idempotency-Key"],
            ),
            create_default_stage=False,
        )
        # The endpoint is public, so cap request rates to limit abuse and cost.
        api_stage = self.api.add_stage(
            "NafnApiDefaultStage",
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=10, burst_limit=20),
        )
        api_domain_name = f"api.{domain_name}"
        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "ApiHostedZone",
            hosted_zone_id=hosted_zone_id,
            zone_name=hosted_zone_name,
        )
        certificate = acm.Certificate(
            self,
            "ApiCertificate",
            domain_name=api_domain_name,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )
        api_domain = apigw.DomainName(
            self,
            "ApiDomainName",
            domain_name=api_domain_name,
            certificate=certificate,
        )
        apigw.ApiMapping(
            self,
            "ApiDomainMapping",
            api=self.api,
            domain_name=api_domain,
            stage=api_stage,
        )
        route53.ARecord(
            self,
            "ApiARecord",
            zone=hosted_zone,
            record_name=f"{api_domain_name}.",
            target=route53.RecordTarget.from_alias(
                route53_targets.ApiGatewayv2DomainProperties(
                    api_domain.regional_domain_name, api_domain.regional_hosted_zone_id
                )
            ),
        )
        self.api.add_routes(
            path="/partners",
            methods=[apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration(
                "NafnCreatePartnerIntegration", self.create_partner_function
            ),
        )

        self.list_partners_function = lambda_.Function(
            self,
            "NafnListPartnersFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="list_partners.handler",
            code=lambda_code,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={"PARTNERS_TABLE_NAME": self.partners_table.table_name},
            log_group=logs.LogGroup(
                self,
                "NafnListPartnersLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        self.partners_table.grant(self.list_partners_function, "dynamodb:Scan")
        self.api.add_routes(
            path="/partners",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpLambdaIntegration(
                "NafnListPartnersIntegration", self.list_partners_function
            ),
        )

        self.create_resource_function = lambda_.Function(
            self,
            "NafnCreateResourceFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="create_resource.handler",
            code=lambda_code,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "RESOURCES_TABLE_NAME": self.resources_table.table_name,
                "PARTNERS_TABLE_NAME": self.partners_table.table_name,
            },
            log_group=logs.LogGroup(
                self,
                "NafnCreateResourceLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        self.resources_table.grant(self.create_resource_function, "dynamodb:PutItem")
        self.partners_table.grant(self.create_resource_function, "dynamodb:GetItem")
        self.api.add_routes(
            path="/resources",
            methods=[apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration(
                "NafnCreateResourceIntegration", self.create_resource_function
            ),
        )

        self.list_resources_function = lambda_.Function(
            self,
            "NafnListResourcesFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="list_resources.handler",
            code=lambda_code,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={"RESOURCES_TABLE_NAME": self.resources_table.table_name},
            log_group=logs.LogGroup(
                self,
                "NafnListResourcesLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        self.resources_table.grant(self.list_resources_function, "dynamodb:Scan")
        self.api.add_routes(
            path="/resources",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpLambdaIntegration(
                "NafnListResourcesIntegration", self.list_resources_function
            ),
        )

        CfnOutput(self, "ApiUrl", value=f"https://{api_domain_name}")
        CfnOutput(self, "PartnersTableName", value=self.partners_table.table_name)
        CfnOutput(self, "ResourcesTableName", value=self.resources_table.table_name)
        CfnOutput(
            self,
            "PartnerEinReservationsTableName",
            value=self.partner_ein_reservations_table.table_name,
        )
