"""ModuleGuard's severity for Checkov rules that arrive without one.

Open-source Checkov reports no severity for most rules. These assignments apply only then
(a severity supplied by Checkov always wins) and are marked severity_source="moduleguard".
Rules not listed stay UNKNOWN: nothing is guessed.

HIGH    deploying as written can directly expose a resource or widen privileges
MEDIUM  a safeguard is missing (encryption, logging, authentication strength, scoping)
LOW     operational hygiene, resilience or manageability
CRITICAL is never assigned here: one static rule cannot show real exposure, since modules
switch most resources by variable. It is reserved for correlated findings.
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
    # --- MEDIUM: missing safeguard
    "CKV_AWS_356": MEDIUM,  # IAM statement resource "*" for restrictable actions
    "CKV_AWS_355": MEDIUM,  # same rule family as 356
    "CKV_AWS_111": MEDIUM,  # IAM write access without constraints
    "CKV2_AWS_12": MEDIUM,  # default security group not locked down (no internet ingress by default)
    "CKV_AWS_58": MEDIUM,  # EKS secrets not envelope-encrypted
    "CKV_AWS_88": MEDIUM,  # EC2 instance with a public IP
    "CKV_AWS_19": MEDIUM,  # S3 encryption at rest
    "CKV_AWS_145": MEDIUM,  # S3 not encrypted with KMS
    "CKV_AWS_27": MEDIUM,  # SQS queue not encrypted
    "CKV_AWS_8": MEDIUM,  # EBS / launch configuration not encrypted
    "CKV_AWS_37": MEDIUM,  # EKS control plane logging incomplete
    "CKV2_AWS_11": MEDIUM,  # VPC flow logging off
    "CKV_AWS_341": MEDIUM,  # metadata hop limit > 1: containers can reach credentials
    "CKV_AWS_9": MEDIUM,  # password policy: expiry
    "CKV_AWS_12": MEDIUM,  # password policy: number
    "CKV_AWS_14": MEDIUM,  # password policy: symbol
    "CKV2_AWS_22": MEDIUM,  # IAM user with console access
    "CKV_AWS_40": MEDIUM,  # IAM policies attached directly to users
    "CKV2_AWS_65": MEDIUM,  # S3 ACLs enabled: accidental object exposure
    "CKV_AWS_148": MEDIUM,  # default VPC provisioned
    # --- LOW: hygiene, resilience, manageability
    "CKV_AWS_273": LOW,  # access not through SSO
    # Fires for every registry module pinned by version tag; supply chain is assessed by TFSC.
    "CKV_TF_1": LOW,
    "CKV_AWS_21": LOW,  # S3 versioning
    "CKV_AWS_144": LOW,  # S3 cross-region replication
    "CKV_AWS_300": LOW,  # S3 lifecycle: abort failed uploads
    "CKV2_AWS_62": LOW,  # S3 event notifications
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
}
SOURCE = "moduleguard"
