"""ModuleGuard's remediation text for Checkov rules that arrive without a guideline.

Checkov runs with --skip-download, so most findings carry no guideline. The normalizer fills
Finding.guideline from this table only when Checkov's own guideline is empty, and marks the
finding raw_data["guideline_source"] = "moduleguard". A guideline supplied by Checkov always wins.

Wording: Korean, one or two sentences, naming the Terraform argument to change where there is
one. Priority went to the rules seen most often when scanning terraform-aws-modules
(docs/severity-mapping.md); every rule in SEVERITY_OVERRIDES has an entry.
"""

from typing import Any

from app.scanners.severity_overrides import SOURCE

GUIDELINE_SOURCE = SOURCE

REMEDIATIONS: dict[str, str] = {
    # --- IAM policies
    "CKV_AWS_356": (
        '제한 가능한 작업에 Resource "*"를 쓰지 말고 대상 리소스 ARN을 명시하세요. '
        "ARN을 특정할 수 없다면 Condition으로 범위를 좁히세요."
    ),
    "CKV_AWS_355": (
        '제한 가능한 작업에 Resource "*"를 쓰지 말고 대상 리소스 ARN을 명시하세요. '
        "ARN을 특정할 수 없다면 Condition으로 범위를 좁히세요."
    ),
    "CKV_AWS_111": (
        "쓰기 권한(Put·Create·Delete 등)은 필요한 리소스 ARN으로 한정하고, "
        "가능하면 aws:ResourceTag 같은 Condition을 추가하세요."
    ),
    "CKV_AWS_290": (
        "쓰기 권한(Put·Create·Delete 등)은 필요한 리소스 ARN으로 한정하고, "
        "가능하면 aws:ResourceTag 같은 Condition을 추가하세요."
    ),
    "CKV_AWS_109": (
        "iam:Put*Policy·iam:Attach*·리소스 정책 변경 같은 권한 관리 작업은 특정 ARN으로 한정하세요. "
        "제약 없이 허용하면 정책을 스스로 넓혀 권한을 상승시킬 수 있습니다."
    ),
    "CKV_AWS_289": (
        "iam:Put*Policy·iam:Attach*·리소스 정책 변경 같은 권한 관리 작업은 특정 ARN으로 한정하세요. "
        "제약 없이 허용하면 정책을 스스로 넓혀 권한을 상승시킬 수 있습니다."
    ),
    "CKV_AWS_62": (
        'Action "*"와 Resource "*"를 함께 허용하는 관리자급 문장을 제거하고 '
        "필요한 작업과 리소스만 나열하세요. 관리자 권한이 꼭 필요하면 모듈 밖에서 명시적으로 부여하세요."
    ),
    "CKV_AWS_1": (
        'aws_iam_policy_document에서 actions = ["*"], resources = ["*"] 조합을 제거하고 '
        "필요한 작업과 리소스만 나열하세요."
    ),
    "CKV_AWS_63": (
        'Action "*"(모든 작업)를 허용하지 말고 서비스별 필요한 작업만 나열하세요. '
        '와일드카드가 필요하면 "s3:Get*"처럼 서비스와 동사 범위를 제한하세요.'
    ),
    "CKV_AWS_49": (
        'aws_iam_policy_document의 actions에 "*"를 쓰지 말고 필요한 작업만 나열하세요. '
        '와일드카드가 필요하면 "s3:Get*"처럼 범위를 제한하세요.'
    ),
    "CKV_AWS_286": (
        "iam:PassRole, iam:CreatePolicyVersion, iam:AttachRolePolicy처럼 권한 상승에 쓰일 수 있는 "
        "작업을 제거하거나 특정 ARN과 Condition으로 제한하세요."
    ),
    "CKV_AWS_110": (
        "iam:PassRole, iam:CreatePolicyVersion, iam:AttachRolePolicy처럼 권한 상승에 쓰일 수 있는 "
        "작업을 제거하거나 특정 ARN과 Condition으로 제한하세요."
    ),
    "CKV_AWS_287": (
        "iam:CreateAccessKey, sts:GetFederationToken처럼 자격 증명을 발급·노출하는 작업을 제거하거나 "
        "특정 사용자·역할 ARN으로 제한하세요."
    ),
    "CKV_AWS_107": (
        "iam:CreateAccessKey, sts:GetFederationToken처럼 자격 증명을 발급·노출하는 작업을 제거하거나 "
        "특정 사용자·역할 ARN으로 제한하세요."
    ),
    "CKV_AWS_288": (
        's3:GetObject, secretsmanager:GetSecretValue처럼 데이터를 읽어 가는 작업을 Resource "*"로 '
        "허용하지 말고 필요한 버킷·시크릿 ARN으로 한정하세요."
    ),
    "CKV_AWS_108": (
        's3:GetObject, secretsmanager:GetSecretValue처럼 데이터를 읽어 가는 작업을 resources = ["*"]로 '
        "허용하지 말고 필요한 ARN으로 한정하세요."
    ),
    "CKV_AWS_283": (
        'Principal을 "*" 또는 AWS = "*"로 두지 말고 접근이 필요한 계정·역할 ARN을 지정하세요. '
        "공개가 의도라면 aws:SourceArn·aws:SourceAccount 같은 Condition을 추가하세요."
    ),
    "CKV_AWS_40": (
        "IAM 정책을 사용자에게 직접 연결하지 말고 그룹이나 역할에 연결한 뒤 사용자를 그룹에 넣으세요."
    ),
    "CKV2_AWS_22": (
        "프로그래밍 접근만 필요한 IAM 사용자에는 aws_iam_user_login_profile을 만들지 마세요. "
        "사람의 콘솔 접근은 IAM Identity Center(SSO)로 관리하세요."
    ),
    "CKV_AWS_273": ("IAM 사용자 대신 IAM Identity Center(SSO)와 역할 기반 접근을 사용하세요."),
    "CKV2_AWS_14": "사용자가 없는 IAM 그룹은 제거하거나 실제 사용자를 멤버로 추가하세요.",
    "CKV2_AWS_21": (
        "IAM 사용자를 aws_iam_user_group_membership 등으로 그룹에 넣고 권한은 그룹 단위로 관리하세요."
    ),
    # --- IAM password policy
    "CKV_AWS_9": "aws_iam_account_password_policy의 max_password_age를 90 이하로 설정하세요.",
    "CKV_AWS_10": (
        "aws_iam_account_password_policy의 minimum_password_length를 14 이상으로 설정하세요."
    ),
    "CKV_AWS_11": (
        "aws_iam_account_password_policy에서 require_lowercase_characters = true로 설정하세요."
    ),
    "CKV_AWS_12": "aws_iam_account_password_policy에서 require_numbers = true로 설정하세요.",
    "CKV_AWS_14": "aws_iam_account_password_policy에서 require_symbols = true로 설정하세요.",
    "CKV_AWS_15": (
        "aws_iam_account_password_policy에서 require_uppercase_characters = true로 설정하세요."
    ),
    # --- S3
    "CKV_AWS_53": "aws_s3_bucket_public_access_block에서 block_public_acls = true로 설정하세요.",
    "CKV_AWS_54": "aws_s3_bucket_public_access_block에서 block_public_policy = true로 설정하세요.",
    "CKV_AWS_55": "aws_s3_bucket_public_access_block에서 ignore_public_acls = true로 설정하세요.",
    "CKV_AWS_56": (
        "aws_s3_bucket_public_access_block에서 restrict_public_buckets = true로 설정하세요."
    ),
    "CKV2_AWS_6": (
        "버킷마다 aws_s3_bucket_public_access_block을 만들고 네 가지 차단 옵션을 모두 true로 설정하세요."
    ),
    "CKV_AWS_20": (
        "public-read·public-read-write ACL을 제거하고 버킷 ACL을 private으로 두세요. "
        "공개 배포가 목적이면 CloudFront OAC를 사용하세요."
    ),
    "CKV_AWS_57": (
        "public-read-write ACL을 즉시 제거하고 private으로 두세요. "
        "누구나 객체를 쓰거나 덮어쓸 수 있는 상태입니다."
    ),
    "CKV_AWS_70": (
        '버킷 정책의 Principal "*" 허용 문장을 제거하거나 특정 계정·역할 ARN으로 바꾸세요. '
        "공개가 의도라면 읽기 작업만 허용하고 Condition을 추가하세요."
    ),
    "CKV_AWS_93": (
        "루트 사용자 외 모든 주체를 Deny하는 버킷 정책은 관리자도 잠글 수 있습니다. "
        "관리용 역할을 예외로 두도록 정책을 조정하세요."
    ),
    "CKV2_AWS_65": (
        'aws_s3_bucket_ownership_controls에서 object_ownership = "BucketOwnerEnforced"로 설정해 '
        "ACL을 비활성화하고 접근은 버킷 정책으로 관리하세요."
    ),
    "CKV_AWS_19": (
        "aws_s3_bucket_server_side_encryption_configuration으로 기본 암호화(SSE-KMS 또는 SSE-S3)를 "
        "설정하세요."
    ),
    "CKV_AWS_145": (
        'aws_s3_bucket_server_side_encryption_configuration에서 sse_algorithm = "aws:kms"와 '
        "kms_master_key_id를 지정해 KMS 키로 암호화하세요."
    ),
    "CKV_AWS_18": ("aws_s3_bucket_logging으로 서버 액세스 로그를 별도 로그 버킷에 남기세요."),
    "CKV_AWS_21": (
        'aws_s3_bucket_versioning에서 status = "Enabled"로 설정해 덮어쓰기·삭제된 객체를 복구할 수 '
        "있게 하세요."
    ),
    "CKV_AWS_144": (
        "재해 복구가 필요한 버킷이면 aws_s3_bucket_replication_configuration으로 다른 리전에 복제하세요. "
        "필요 없다면 이 탐지는 운영 판단으로 무시해도 됩니다."
    ),
    "CKV_AWS_300": (
        "aws_s3_bucket_lifecycle_configuration 규칙에 abort_incomplete_multipart_upload"
        "(days_after_initiation)를 추가하세요."
    ),
    "CKV2_AWS_61": (
        "aws_s3_bucket_lifecycle_configuration으로 객체 만료·스토리지 클래스 전환 규칙을 정의하세요."
    ),
    "CKV2_AWS_62": (
        "변경 감지가 필요하면 aws_s3_bucket_notification으로 SNS·SQS·Lambda·EventBridge 알림을 "
        "설정하세요."
    ),
    # --- network
    "CKV_AWS_24": (
        "0.0.0.0/0에서 22번 포트(SSH) 인바운드를 허용하지 말고 관리용 CIDR로 한정하거나 "
        "SSM Session Manager를 사용하세요."
    ),
    "CKV_AWS_25": (
        "0.0.0.0/0에서 3389번 포트(RDP) 인바운드를 허용하지 말고 관리용 CIDR이나 VPN·Bastion으로 "
        "한정하세요."
    ),
    "CKV_AWS_260": (
        "0.0.0.0/0에서 80번 포트 인바운드를 직접 허용하지 말고 로드밸런서 보안 그룹을 소스로 지정하세요. "
        "공개 웹 서비스라면 HTTPS로 리디렉션하세요."
    ),
    "CKV_AWS_277": (
        "0.0.0.0/0에서 모든 포트(-1) 인바운드를 허용하는 규칙을 제거하고 필요한 포트와 소스만 허용하세요."
    ),
    "CKV_AWS_382": (
        "모든 포트로의 0.0.0.0/0 아웃바운드를 기본으로 열지 말고 필요한 목적지·포트만 허용하세요. "
        "AWS 서비스 접근은 VPC 엔드포인트를 고려하세요."
    ),
    "CKV2_AWS_12": (
        "aws_default_security_group을 ingress·egress 규칙 없이 선언해 VPC 기본 보안 그룹의 "
        "모든 트래픽을 막으세요."
    ),
    "CKV_AWS_23": "보안 그룹과 각 규칙에 description을 적어 허용 목적을 남기세요.",
    "CKV2_AWS_5": (
        "어떤 리소스에도 연결되지 않은 보안 그룹은 제거하거나 사용할 리소스에 연결하세요. "
        "모듈 외부에서 연결하도록 출력하는 경우라면 무시해도 됩니다."
    ),
    "CKV2_AWS_11": ("aws_flow_log로 VPC 흐름 로그를 CloudWatch Logs나 S3에 남기세요."),
    "CKV_AWS_148": (
        "aws_default_vpc를 선언하지 말고 필요한 네트워크는 명시적인 aws_vpc로 만드세요."
    ),
    "CKV_AWS_394": (
        "aws_availability_zones 데이터 소스에 filter나 exclude_zone_ids로 사용할 AZ를 고정해 "
        "AWS가 AZ를 추가해도 결과가 바뀌지 않게 하세요."
    ),
    "CKV2_AWS_19": "인스턴스에 연결되지 않은 EIP는 해제하거나 aws_eip_association으로 연결하세요.",
    # --- EC2 / launch templates
    "CKV_AWS_79": ('metadata_options에서 http_tokens = "required"로 설정해 IMDSv2만 허용하세요.'),
    "CKV_AWS_341": (
        "metadata_options의 http_put_response_hop_limit를 1로 설정하세요. "
        "컨테이너가 노드 IMDS가 꼭 필요하면 2로 두되 IRSA 등 대안을 우선하세요."
    ),
    "CKV_AWS_8": (
        "EBS 볼륨(root_block_device·ebs_block_device·block_device_mappings)에 encrypted = true를 "
        "설정하세요."
    ),
    "CKV_AWS_88": (
        "associate_public_ip_address = false로 두고 외부 접근은 로드밸런서나 NAT를 거치게 하세요."
    ),
    "CKV_AWS_126": "인스턴스에 monitoring = true를 설정해 1분 단위 상세 모니터링을 켜세요.",
    "CKV_AWS_135": "지원 인스턴스 유형이면 ebs_optimized = true로 설정하세요.",
    "CKV2_AWS_41": (
        "EC2 인스턴스에 iam_instance_profile을 연결해 장기 자격 증명 없이 AWS API를 호출하게 하세요."
    ),
    # --- EKS
    "CKV_AWS_38": (
        "public endpoint를 써야 한다면 public_access_cidrs를 관리용 CIDR로 제한하세요. "
        "0.0.0.0/0이면 누구나 클러스터 API에 접근을 시도할 수 있습니다."
    ),
    "CKV_AWS_39": (
        "vpc_config에서 endpoint_public_access = false, endpoint_private_access = true로 두세요. "
        "공개가 필요하면 public_access_cidrs를 제한하세요."
    ),
    "CKV_AWS_58": (
        "encryption_config에 KMS 키를 지정해 Kubernetes Secret을 envelope 암호화하세요."
    ),
    "CKV_AWS_37": (
        "enabled_cluster_log_types에 api, audit, authenticator, controllerManager, scheduler를 "
        "모두 포함하세요."
    ),
    # --- RDS
    "CKV_AWS_17": ("publicly_accessible = false로 두고 DB를 프라이빗 서브넷에 배치하세요."),
    "CKV_AWS_16": (
        "storage_encrypted = true로 설정하고 필요하면 kms_key_id로 고객 관리형 키를 지정하세요."
    ),
    "CKV_AWS_129": (
        "enabled_cloudwatch_logs_exports에 엔진별 로그(예: MySQL은 audit·error·general·slowquery, "
        "PostgreSQL은 postgresql)를 지정하세요."
    ),
    "CKV_AWS_353": (
        "performance_insights_enabled = true로 설정하고 필요하면 "
        "performance_insights_kms_key_id를 지정하세요."
    ),
    "CKV_AWS_118": (
        "monitoring_interval을 0보다 크게(예: 60) 설정하고 monitoring_role_arn을 지정해 "
        "Enhanced Monitoring을 켜세요."
    ),
    "CKV_AWS_157": "운영 DB는 multi_az = true로 설정해 장애 조치를 지원하세요.",
    "CKV_AWS_293": ("deletion_protection = true로 설정해 실수로 DB가 삭제되지 않게 하세요."),
    "CKV_AWS_133": ("backup_retention_period를 1 이상(권장 7 이상)으로 설정해 자동 백업을 켜세요."),
    "CKV_AWS_226": "auto_minor_version_upgrade = true로 설정해 보안 패치를 자동 적용하세요.",
    "CKV_AWS_211": (
        "ca_cert_identifier를 현재 CA(예: rds-ca-rsa2048-g1)로 지정하세요. "
        "만료된 CA를 쓰면 TLS 연결이 실패할 수 있습니다."
    ),
    "CKV2_AWS_60": "copy_tags_to_snapshot = true로 설정해 스냅샷에도 태그를 남기세요.",
    # --- secrets, keys, logs
    "CKV_AWS_304": (
        "aws_secretsmanager_secret_rotation의 rotation_rules를 90일 이하"
        "(automatically_after_days 또는 schedule_expression)로 설정하세요."
    ),
    "CKV2_AWS_57": (
        "aws_secretsmanager_secret_rotation으로 자동 교체 Lambda와 rotation_rules를 설정하세요."
    ),
    "CKV_AWS_149": "aws_secretsmanager_secret에 kms_key_id로 고객 관리형 KMS 키를 지정하세요.",
    "CKV_AWS_338": (
        "aws_cloudwatch_log_group의 retention_in_days를 365 이상으로 설정하세요. "
        "비용 때문에 짧게 둔다면 장기 보관 위치로 내보내세요."
    ),
    "CKV_AWS_158": "aws_cloudwatch_log_group에 kms_key_id를 지정해 로그를 KMS로 암호화하세요.",
    # --- Lambda
    "CKV_AWS_258": (
        'aws_lambda_function_url의 authorization_type을 "AWS_IAM"으로 바꾸세요. '
        '"NONE"이면 URL을 아는 누구나 함수를 호출할 수 있습니다.'
    ),
    "CKV_AWS_45": (
        "환경 변수에 비밀 값을 넣지 말고 Secrets Manager나 SSM SecureString에서 런타임에 읽으세요. "
        "이미 커밋된 값은 교체하세요."
    ),
    "CKV_AWS_173": "환경 변수가 있으면 kms_key_arn을 지정해 고객 관리형 키로 암호화하세요.",
    "CKV_AWS_272": (
        "aws_lambda_code_signing_config를 만들고 code_signing_config_arn으로 연결해 서명된 코드만 "
        "배포되게 하세요."
    ),
    "CKV_AWS_50": 'tracing_config에서 mode = "Active"로 설정해 X-Ray 추적을 켜세요.',
    "CKV_AWS_115": (
        "reserved_concurrent_executions를 설정해 한 함수가 계정 동시성을 모두 쓰지 않게 하세요."
    ),
    "CKV_AWS_116": (
        "dead_letter_config에 SQS 큐나 SNS 토픽을 지정해 실패한 비동기 호출을 보존하세요."
    ),
    "CKV_AWS_117": (
        "VPC 내부 리소스에 접근하는 함수라면 vpc_config로 서브넷과 보안 그룹을 지정하세요."
    ),
    "CKV_AWS_363": "지원이 끝난 runtime을 현재 지원되는 버전으로 올리세요.",
    # --- other data stores and messaging
    "CKV_AWS_27": (
        "aws_sqs_queue에 kms_master_key_id를 지정하거나 sqs_managed_sse_enabled = true로 설정하세요."
    ),
    "CKV_AWS_26": "aws_sns_topic에 kms_master_key_id를 지정해 저장 메시지를 암호화하세요.",
    "CKV_AWS_42": "aws_efs_file_system에 encrypted = true를 설정하세요.",
    "CKV_AWS_184": "aws_efs_file_system에 kms_key_id로 고객 관리형 KMS 키를 지정하세요.",
    "CKV_AWS_43": ('aws_kinesis_stream에 encryption_type = "KMS"와 kms_key_id를 설정하세요.'),
    "CKV_AWS_185": (
        "aws_kinesis_stream의 kms_key_id에 AWS 관리형(alias/aws/kinesis) 대신 고객 관리형 키를 "
        "지정하세요."
    ),
    "CKV_AWS_119": (
        "aws_dynamodb_table의 server_side_encryption에서 enabled = true와 kms_key_arn을 지정하세요."
    ),
    "CKV_AWS_28": ("aws_dynamodb_table에 point_in_time_recovery { enabled = true }를 추가하세요."),
    "CKV_AWS_209": (
        "aws_mq_broker의 encryption_options에서 use_aws_owned_key = false와 kms_key_id를 지정하세요."
    ),
    "CKV_AWS_48": "aws_mq_broker의 logs 블록에서 general(필요하면 audit) 로그를 켜세요.",
    "CKV_AWS_207": "aws_mq_broker에 auto_minor_version_upgrade = true를 설정하세요.",
    "CKV_AWS_136": (
        'aws_ecr_repository의 encryption_configuration에서 encryption_type = "KMS"를 설정하세요.'
    ),
    "CKV_AWS_51": (
        'image_tag_mutability = "IMMUTABLE"로 설정해 같은 태그로 다른 이미지를 덮어쓰지 못하게 하세요.'
    ),
    "CKV_AWS_163": ("image_scanning_configuration에서 scan_on_push = true로 설정하세요."),
    # --- module sources
    "CKV_TF_1": (
        "외부 모듈 source를 커밋 해시(?ref=<40자리 SHA>)로 고정하면 태그 변경에 영향받지 않습니다. "
        "레지스트리 모듈의 버전 고정은 공급망 탭(TFSC) 결과를 함께 확인하세요."
    ),
    "CKV_TF_2": ("외부 모듈 source를 브랜치가 아닌 버전 태그나 커밋 해시로 고정하세요."),
}


def has_guideline(value: Any) -> bool:
    """A guideline counts only when it is a non-blank string."""
    return isinstance(value, str) and bool(value.strip())
