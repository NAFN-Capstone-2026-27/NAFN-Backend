from aws_cdk import CfnOutput, RemovalPolicy, Stack, aws_dynamodb as dynamodb
from constructs import Construct


class DataStack(Stack):
    """Own the environment's DynamoDB tables independently of the API."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
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

        CfnOutput(self, "PartnersTableName", value=self.partners_table.table_name)
        CfnOutput(self, "ResourcesTableName", value=self.resources_table.table_name)
