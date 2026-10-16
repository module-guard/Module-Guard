from dataclasses import dataclass
from enum import StrEnum

from app.parsers.domain import EntityType, TerraformEntity


class FlowSourceCategory(StrEnum):
    USER_INPUT = "USER_INPUT"
    SECRET_SOURCE = "SECRET_SOURCE"
    DATA_SOURCE = "DATA_SOURCE"
    MODULE_OUTPUT = "MODULE_OUTPUT"
    RESOURCE_ATTRIBUTE = "RESOURCE_ATTRIBUTE"
    LOCAL_VALUE = "LOCAL_VALUE"
    UNKNOWN = "UNKNOWN"


class FlowSourceClassifier:
    def __init__(self, secret_types: frozenset[str] | None = None):
        self.secret_types = (
            secret_types
            if secret_types is not None
            else frozenset(
                {
                    "aws_ssm_parameter",
                    "aws_secretsmanager_secret",
                    "aws_secretsmanager_secret_version",
                    "azurerm_key_vault_secret",
                    "google_secret_manager_secret_version",
                }
            )
        )

    def classify(self, entity: TerraformEntity | None) -> FlowSourceCategory:
        if entity is None:
            return FlowSourceCategory.UNKNOWN
        if entity.entity_type == EntityType.DATA:
            return (
                FlowSourceCategory.SECRET_SOURCE
                if entity.resource_type in self.secret_types
                else FlowSourceCategory.DATA_SOURCE
            )
        return {
            EntityType.VARIABLE: FlowSourceCategory.USER_INPUT,
            EntityType.LOCAL: FlowSourceCategory.LOCAL_VALUE,
            EntityType.MODULE: FlowSourceCategory.MODULE_OUTPUT,
            EntityType.OUTPUT: FlowSourceCategory.MODULE_OUTPUT,
            EntityType.RESOURCE: FlowSourceCategory.RESOURCE_ATTRIBUTE,
        }.get(entity.entity_type, FlowSourceCategory.UNKNOWN)


@dataclass(frozen=True)
class SensitiveSinkRule:
    resource_type: str
    attribute: str
    category: str


# Attribute names follow the provider schemas (hashicorp/aws, google, azurerm). Nested blocks
# such as aws_security_group.ingress are matched by their root attribute name.
DEFAULT_SINK_RULES = (
    # CODE_EXECUTION: values executed or interpreted on instance boot.
    SensitiveSinkRule("aws_instance", "user_data", "CODE_EXECUTION"),
    SensitiveSinkRule("aws_instance", "user_data_base64", "CODE_EXECUTION"),
    SensitiveSinkRule("aws_launch_template", "user_data", "CODE_EXECUTION"),
    SensitiveSinkRule("aws_launch_configuration", "user_data", "CODE_EXECUTION"),
    SensitiveSinkRule("google_compute_instance", "metadata_startup_script", "CODE_EXECUTION"),
    SensitiveSinkRule("azurerm_linux_virtual_machine", "custom_data", "CODE_EXECUTION"),
    # IAM_POLICY: policy documents that grant or delegate permissions.
    SensitiveSinkRule("aws_iam_policy", "policy", "IAM_POLICY"),
    SensitiveSinkRule("aws_iam_role_policy", "policy", "IAM_POLICY"),
    SensitiveSinkRule("aws_iam_role", "assume_role_policy", "IAM_POLICY"),
    SensitiveSinkRule("aws_iam_user_policy", "policy", "IAM_POLICY"),
    SensitiveSinkRule("aws_iam_group_policy", "policy", "IAM_POLICY"),
    SensitiveSinkRule("aws_s3_bucket_policy", "policy", "IAM_POLICY"),
    SensitiveSinkRule("aws_kms_key", "policy", "IAM_POLICY"),
    # NETWORK: exposure and reachability settings.
    SensitiveSinkRule("aws_security_group", "ingress", "NETWORK"),
    SensitiveSinkRule("aws_security_group", "egress", "NETWORK"),
    SensitiveSinkRule("aws_security_group_rule", "cidr_blocks", "NETWORK"),
    SensitiveSinkRule("aws_vpc_security_group_ingress_rule", "cidr_ipv4", "NETWORK"),
    SensitiveSinkRule("aws_db_instance", "publicly_accessible", "NETWORK"),
    SensitiveSinkRule("aws_instance", "associate_public_ip_address", "NETWORK"),
    SensitiveSinkRule("google_compute_firewall", "source_ranges", "NETWORK"),
    SensitiveSinkRule("azurerm_network_security_rule", "source_address_prefix", "NETWORK"),
    # CREDENTIAL: passwords and tokens configured on managed services.
    SensitiveSinkRule("aws_db_instance", "password", "CREDENTIAL"),
    SensitiveSinkRule("aws_rds_cluster", "master_password", "CREDENTIAL"),
    SensitiveSinkRule("aws_docdb_cluster", "master_password", "CREDENTIAL"),
    SensitiveSinkRule("aws_redshift_cluster", "master_password", "CREDENTIAL"),
    SensitiveSinkRule("aws_elasticache_replication_group", "auth_token", "CREDENTIAL"),
    SensitiveSinkRule("azurerm_linux_virtual_machine", "admin_password", "CREDENTIAL"),
    SensitiveSinkRule("google_sql_user", "password", "CREDENTIAL"),
    # ENVIRONMENT / COMMAND / SECRET_STORAGE
    SensitiveSinkRule("aws_lambda_function", "environment", "ENVIRONMENT"),
    SensitiveSinkRule("aws_ecs_task_definition", "container_definitions", "COMMAND"),
    SensitiveSinkRule("aws_ssm_parameter", "value", "SECRET_STORAGE"),
    SensitiveSinkRule("aws_secretsmanager_secret_version", "secret_string", "SECRET_STORAGE"),
)


def is_sensitive_input(entity: TerraformEntity | None) -> bool:
    """`sensitive = true` on a variable; the source category itself stays USER_INPUT."""
    return bool(
        entity is not None
        and entity.entity_type == EntityType.VARIABLE
        and str(entity.attributes.get("sensitive", "")).strip().lower() == "true"
    )


class FlowSinkClassifier:
    def __init__(self, rules: tuple[SensitiveSinkRule, ...] = DEFAULT_SINK_RULES):
        self.rules = {(r.resource_type, r.attribute): r.category for r in rules}

    def classify(self, entity: TerraformEntity | None, attribute: str | None) -> str:
        if entity is None or entity.entity_type != EntityType.RESOURCE or not attribute:
            return "UNKNOWN"
        root = attribute.split(".", 1)[0].split("[", 1)[0]
        return self.rules.get((entity.resource_type, root), "UNKNOWN")
