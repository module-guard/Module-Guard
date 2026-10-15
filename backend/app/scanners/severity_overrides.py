"""ModuleGuard's severity for Checkov rules that arrive without one.

Open-source Checkov reports no severity for most rules. These assignments apply only then
(a severity supplied by Checkov always wins) and are marked severity_source="moduleguard".
Rules not listed stay UNKNOWN: nothing is guessed.

HIGH    deploying as written can directly expose a resource or widen privileges
MEDIUM  a safeguard is missing (encryption, logging, authentication strength, scoping)
LOW     operational hygiene, resilience or manageability
CRITICAL is never assigned here: one static rule cannot show real exposure, since modules
switch most resources by variable. It is reserved for correlated findings.

Every entry was checked against the rule implementation in the pinned Checkov
(requirements-scanner.txt). Coverage measurement and how to re-apply: docs/severity-mapping.md.
"""

from app.models.enums import Severity

HIGH, MEDIUM, LOW = Severity.HIGH, Severity.MEDIUM, Severity.LOW

SEVERITY_OVERRIDES: dict[str, Severity] = {
    # --- HIGH: exposure or privilege escalation
    "CKV_AWS_109": HIGH,  # IAM permissions management without constraints: escalation path
    "CKV_AWS_38": HIGH,  # EKS public endpoint open to 0.0.0.0/0
    "CKV_AWS_39": HIGH,  # EKS public endpoint enabled: cluster API on the internet
    "CKV_AWS_53": HIGH,  # S3 block public ACLs off
    "CKV_AWS_54": HIGH,  # S3 block public policy off
    "CKV_AWS_55": HIGH,  # S3 ignore public ACLs off
    "CKV_AWS_56": HIGH,  # S3 restrict public buckets off
    "CKV2_AWS_6": HIGH,  # S3 bucket without a public access block
    "CKV_AWS_79": HIGH,  # IMDSv1 allowed: SSRF can steal instance credentials
    # aws_iam_{role,user,group}_policy, aws_iam_policy and the SSO inline policy. 62 fails on an
    # Allow statement with Action "*" and Resource "*" (full admin); 63 on Action "*" for any
    # resource. Both are TFCR001's privileged-IAM allowlist (app/correlation/scoring.py).
    "CKV_AWS_62": HIGH,
    "CKV_AWS_63": HIGH,
    "CKV_AWS_1": HIGH,  # 62 for data aws_iam_policy_document (not in TFCR001's allowlist)
    "CKV_AWS_49": HIGH,  # 63 for data aws_iam_policy_document (not in TFCR001's allowlist)
    "CKV_AWS_286": HIGH,  # IAM policy resource allows privilege escalation
    "CKV_AWS_110": HIGH,  # 286 for data aws_iam_policy_document
    "CKV_AWS_287": HIGH,  # IAM policy resource allows credentials exposure
    "CKV_AWS_107": HIGH,  # 287 for data aws_iam_policy_document
    "CKV_AWS_289": HIGH,  # 109 for IAM policy resources
    "CKV_AWS_283": HIGH,  # policy document grants any AWS principal ("*")
    "CKV_AWS_70": HIGH,  # S3 bucket policy allows an action to any principal
    "CKV_AWS_20": HIGH,  # S3 ACL grants public READ
    "CKV_AWS_57": HIGH,  # S3 ACL grants public WRITE
    "CKV_AWS_17": HIGH,  # RDS instance publicly accessible
    "CKV_AWS_24": HIGH,  # security group ingress from 0.0.0.0/0 to port 22
    "CKV_AWS_25": HIGH,  # security group ingress from 0.0.0.0/0 to port 3389
    "CKV_AWS_260": HIGH,  # security group ingress from 0.0.0.0/0 to port 80
    "CKV_AWS_277": HIGH,  # security group ingress from 0.0.0.0/0 to every port
    "CKV_AWS_258": HIGH,  # Lambda function URL authorization_type NONE: anyone can invoke it
    "CKV_AWS_45": HIGH,  # secret-looking value hard-coded in Lambda environment variables
    # --- MEDIUM: missing safeguard
    "CKV_AWS_356": MEDIUM,  # IAM statement resource "*" for restrictable actions
    "CKV_AWS_355": MEDIUM,  # same rule family as 356
    "CKV_AWS_111": MEDIUM,  # IAM write access without constraints
    "CKV_AWS_290": MEDIUM,  # 111 for IAM policy resources
    "CKV_AWS_288": MEDIUM,  # IAM policy resource allows reading data without constraints
    "CKV_AWS_108": MEDIUM,  # 288 for data aws_iam_policy_document
    "CKV2_AWS_12": MEDIUM,  # default security group not locked down (no internet ingress by default)
    "CKV_AWS_382": MEDIUM,  # egress to 0.0.0.0/0 on every port: outbound not scoped
    "CKV_AWS_58": MEDIUM,  # EKS secrets not envelope-encrypted
    "CKV_AWS_88": MEDIUM,  # EC2 instance with a public IP
    "CKV_AWS_19": MEDIUM,  # S3 encryption at rest
    "CKV_AWS_145": MEDIUM,  # S3 not encrypted with KMS
    "CKV_AWS_18": MEDIUM,  # S3 access logging off
    "CKV_AWS_27": MEDIUM,  # SQS queue not encrypted
    "CKV_AWS_26": MEDIUM,  # SNS topic not encrypted
    "CKV_AWS_8": MEDIUM,  # EBS / launch configuration not encrypted
    "CKV_AWS_16": MEDIUM,  # RDS storage not encrypted
    "CKV_AWS_129": MEDIUM,  # RDS log exports off
    "CKV_AWS_42": MEDIUM,  # EFS not encrypted
    "CKV_AWS_184": MEDIUM,  # EFS not encrypted with a customer managed key
    "CKV_AWS_43": MEDIUM,  # Kinesis stream not encrypted
    "CKV_AWS_185": MEDIUM,  # Kinesis stream not encrypted with a customer managed key
    "CKV_AWS_119": MEDIUM,  # DynamoDB table not encrypted with a customer managed key
    "CKV_AWS_158": MEDIUM,  # CloudWatch log group not encrypted with KMS
    "CKV_AWS_149": MEDIUM,  # Secrets Manager secret not encrypted with a customer managed key
    "CKV_AWS_209": MEDIUM,  # MQ broker not encrypted with a customer managed key
    "CKV_AWS_48": MEDIUM,  # MQ broker logging off
    "CKV_AWS_136": MEDIUM,  # ECR repository not encrypted with KMS
    "CKV_AWS_51": MEDIUM,  # ECR tags mutable: a pushed tag can be silently replaced
    "CKV_AWS_163": MEDIUM,  # ECR scan on push off: vulnerable images go unnoticed
    "CKV_AWS_173": MEDIUM,  # Lambda environment variables not encrypted with KMS
    "CKV_AWS_272": MEDIUM,  # Lambda code signing not enforced
    "CKV_AWS_304": MEDIUM,  # Secrets Manager rotation interval over 90 days
    "CKV2_AWS_57": MEDIUM,  # Secrets Manager secret without automatic rotation
    "CKV_AWS_37": MEDIUM,  # EKS control plane logging incomplete
    "CKV2_AWS_11": MEDIUM,  # VPC flow logging off
    "CKV_AWS_341": MEDIUM,  # metadata hop limit > 1: containers can reach credentials
    "CKV_AWS_9": MEDIUM,  # password policy: expiry
    "CKV_AWS_10": MEDIUM,  # password policy: minimum length 14
    "CKV_AWS_11": MEDIUM,  # password policy: lowercase
    "CKV_AWS_12": MEDIUM,  # password policy: number
    "CKV_AWS_14": MEDIUM,  # password policy: symbol
    "CKV_AWS_15": MEDIUM,  # password policy: uppercase
    "CKV2_AWS_22": MEDIUM,  # IAM user with console access
    "CKV_AWS_40": MEDIUM,  # IAM policies attached directly to users
    "CKV2_AWS_65": MEDIUM,  # S3 ACLs enabled: accidental object exposure
    "CKV_AWS_148": MEDIUM,  # default VPC provisioned
    # --- LOW: hygiene, resilience, manageability
    "CKV_AWS_273": LOW,  # access not through SSO
    # Fires for every registry module pinned by version tag; supply chain is assessed by TFSC.
    "CKV_TF_1": LOW,
    "CKV_TF_2": LOW,  # module source not pinned to a version tag; supply chain is TFSC's job
    "CKV_AWS_21": LOW,  # S3 versioning
    "CKV_AWS_144": LOW,  # S3 cross-region replication
    "CKV_AWS_300": LOW,  # S3 lifecycle: abort failed uploads
    "CKV2_AWS_61": LOW,  # S3 bucket without a lifecycle configuration
    "CKV2_AWS_62": LOW,  # S3 event notifications
    "CKV_AWS_93": LOW,  # S3 bucket policy denies everyone but root: lockout risk
    "CKV_AWS_338": LOW,  # CloudWatch log retention under one year
    "CKV_AWS_394": LOW,  # availability zones not pinned
    "CKV_AWS_23": LOW,  # security group or rule without description
    "CKV2_AWS_5": LOW,  # security group not attached to a resource
    "CKV2_AWS_19": LOW,  # EIP not attached to an instance
    "CKV_AWS_126": LOW,  # EC2 detailed monitoring
    "CKV_AWS_135": LOW,  # EC2 not EBS optimized
    "CKV2_AWS_41": LOW,  # EC2 instance without an IAM role
    "CKV2_AWS_14": LOW,  # IAM group without users
    "CKV2_AWS_21": LOW,  # IAM user outside any group
    "CKV_AWS_353": LOW,  # RDS Performance Insights off
    "CKV_AWS_118": LOW,  # RDS enhanced monitoring off
    "CKV_AWS_157": LOW,  # RDS not Multi-AZ
    "CKV_AWS_293": LOW,  # RDS deletion protection off
    "CKV_AWS_133": LOW,  # RDS backup_retention_period set to 0: automated backups off
    "CKV_AWS_226": LOW,  # RDS automatic minor version upgrades off
    "CKV_AWS_211": LOW,  # RDS ca_cert_identifier on a retired CA
    "CKV2_AWS_60": LOW,  # RDS snapshots do not copy tags
    "CKV_AWS_28": LOW,  # DynamoDB point-in-time recovery off
    "CKV_AWS_207": LOW,  # MQ automatic minor version upgrades off
    "CKV_AWS_50": LOW,  # Lambda X-Ray tracing off
    "CKV_AWS_115": LOW,  # Lambda without a reserved concurrency limit
    "CKV_AWS_116": LOW,  # Lambda without a dead-letter queue
    "CKV_AWS_117": LOW,  # Lambda not attached to a VPC
    "CKV_AWS_363": LOW,  # Lambda runtime deprecated
}
SOURCE = "moduleguard"
