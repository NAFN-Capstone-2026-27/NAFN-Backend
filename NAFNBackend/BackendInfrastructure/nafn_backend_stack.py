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
    aws_ec2 as ec2,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_rds as rds,
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
        db_instance_class: str,
        db_instance_size: str,
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
            environment={"PARTNERS_TABLE_NAME": self.partners_table.table_name},
            log_group=logs.LogGroup(
                self,
                "NafnCreatePartnerLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        self.partners_table.grant(
            self.create_partner_function, "dynamodb:PutItem", "dynamodb:Scan"
        )

        self.api = apigw.HttpApi(
            self,
            "NafnApi",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=(
                    cors_allow_origins if cors_allow_origins is not None else ["*"]
                ),
                allow_methods=[apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.GET],
                allow_headers=["Content-Type"],
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

        # Set up private VPC network, database does not need internet access
        self.vpc = ec2.Vpc(
            self,
            "NafnVpc",
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # Firewall for the database, starts fully closed. Open port 5432 to
        # Lambdas later with self.database.connections.allow_default_port_from(fn).
        self.db_security_group = ec2.SecurityGroup(
            self,
            "NafnDbSecurityGroup",
            vpc=self.vpc,
            description="NAFN Postgres access",
            allow_all_outbound=False,
        )

        # Postgres database
        self.database = rds.DatabaseInstance(
            self,
            "NafnPostgres",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_18
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass[db_instance_class], ec2.InstanceSize[db_instance_size]
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[self.db_security_group],
            # Password is generated and stored in Secrets Manager, never in code.
            credentials=rds.Credentials.from_generated_secret("nafn_admin"),
            database_name="nafn",
            allocated_storage=20,  # GB, minimum size for POSTGRES
            max_allocated_storage=50,
            storage_encrypted=True,
            multi_az=False,
            publicly_accessible=False,
            # For experimentation, may have to change to true once moved to production
            deletion_protection=False,
            # Take a final snapshot instead of losing data if the DB is removed.
            removal_policy=RemovalPolicy.SNAPSHOT,
        )

        # Print the DB address and password location after deploy
        CfnOutput(self, "DbEndpoint", value=self.database.db_instance_endpoint_address)
        CfnOutput(self, "DbSecretArn", value=self.database.secret.secret_arn)

        CfnOutput(self, "ApiUrl", value=f"https://{api_domain_name}")
        CfnOutput(self, "PartnersTableName", value=self.partners_table.table_name)
        CfnOutput(self, "ResourcesTableName", value=self.resources_table.table_name)
