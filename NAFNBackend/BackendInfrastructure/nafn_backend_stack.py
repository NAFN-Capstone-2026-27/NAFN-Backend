from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_ec2 as ec2,
    aws_rds as rds,
)
from constructs import Construct

LAMBDA_DIR = Path(__file__).parent / "lambda"


class NafnBackendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # One item per partner organization. EIN is unique per org, so it's the
        # partition key; this also lets the API reject duplicate sign-ups.
        self.partners_table = dynamodb.TableV2(
            self,
            "NafnPartnersTable",
            partition_key=dynamodb.Attribute(
                name="ein", type=dynamodb.AttributeType.STRING
            ),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            # Keep partner data if the table is ever removed from the stack.
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.create_partner_function = lambda_.Function(
            self,
            "NafnCreatePartnerFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="create_partner.handler",
            code=lambda_.Code.from_asset(str(LAMBDA_DIR)),
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
        # Write-only: the function never needs to read or delete partners.
        self.partners_table.grant(self.create_partner_function, "dynamodb:PutItem")

        self.api = apigw.HttpApi(
            self,
            "NafnApi",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.GET],
                allow_headers=["Content-Type"],
            ),
            create_default_stage=False,
        )
        # The endpoint is public, so cap request rates to limit abuse and cost.
        self.api.add_stage(
            "NafnApiDefaultStage",
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=10, burst_limit=20),
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
            code=lambda_.Code.from_asset(str(LAMBDA_DIR)),
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

        # Set up private VPC network, database does not need internet access
        self.vpc = ec2.Vpc(
            self, "NafnVpc",
            max_azs=2,  # RDS needs use of at least 2 AZ's
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
            self, "NafnDbSecurityGroup",
            vpc=self.vpc,
            description="NAFN Postgres access",
            allow_all_outbound=False,
        )

        # Postgres database
        self.database = rds.DatabaseInstance(
            self, "NafnPostgres",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[self.db_security_group],
            # Password is generated and stored in Secrets Manager, never in code.
            credentials=rds.Credentials.from_generated_secret("nafn_admin"),
            database_name="nafn",
            allocated_storage=20,   #GB, minimum size for POSTGRES
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

        CfnOutput(self, "ApiUrl", value=self.api.api_endpoint)
        # Used by post_deployment_tests.py to clean up its dummy partner.
        CfnOutput(self, "PartnersTableName", value=self.partners_table.table_name)