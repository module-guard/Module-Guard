import logging
from time import perf_counter

from app.correlation.context import CorrelationContext
from app.correlation.domain import CorrelationResult
from app.correlation.rules import PRIORITIES, CorrelationRule, default_rules

logger = logging.getLogger(__name__)


class CorrelationEngine:
    def __init__(self, rules: list[CorrelationRule] | None = None):
        self.rules = rules if rules is not None else default_rules()

    def evaluate(self, context: CorrelationContext) -> CorrelationResult:
        started = perf_counter()
        result = CorrelationResult()
        logger.info("scan_id=%s action=correlation_start", context.scan_id)
        context.prepare()
        selected = {}
        for rule in sorted(self.rules, key=lambda r: PRIORITIES.get(r.rule_id, 100)):
            result.summary.rules_evaluated += 1
            try:
                matches = rule.evaluate(context)
            except Exception as error:
                result.summary.rule_errors.append(rule.rule_id)
                result.diagnostics.append(
                    {
                        "code": "CORRELATION_RULE_FAILED",
                        "rule_id": rule.rule_id,
                        "error_type": type(error).__name__,
                    }
                )
                logger.error(
                    "scan_id=%s action=correlation_rule_failed rule=%s error_type=%s",
                    context.scan_id,
                    rule.rule_id,
                    type(error).__name__,
                )
                continue
            logger.info(
                "scan_id=%s action=correlation_rule rule=%s matches=%s",
                context.scan_id,
                rule.rule_id,
                len(matches),
            )
            for finding in matches:
                evidence = finding.raw_data
                # One most-specific risk per source/sink; alternate paths do not multiply findings.
                key = (evidence["source"], evidence["sink"])
                if key in selected:
                    result.summary.suppressed += 1
                    previous = selected[key]
                    suppressed = previous.raw_data.setdefault("suppressed_rule_ids", [])
                    if rule.rule_id != previous.rule_id and rule.rule_id not in suppressed:
                        suppressed.append(rule.rule_id)
                    continue
                evidence["dedup_key"] = [finding.rule_id, *key, evidence.get("checkov_rule")]
                selected[key] = finding
        result.findings = list(selected.values())
        summary = result.summary
        summary.findings = len(result.findings)
        summary.paths_evaluated = context.paths_evaluated
        summary.traversal_steps = context.traversal_steps
        summary.truncated = context.truncated
        for finding in result.findings:
            name = finding.severity.value.lower()
            setattr(summary, name, getattr(summary, name) + 1)
        if context.truncated:
            result.diagnostics.append(
                {
                    "code": "CORRELATION_TRAVERSAL_LIMIT",
                    "message": "Bounded path search stopped; correlation coverage is incomplete.",
                }
            )
        summary.duration_ms = int((perf_counter() - started) * 1000)
        logger.info(
            "scan_id=%s action=correlation_complete findings=%s duration_ms=%s",
            context.scan_id,
            summary.findings,
            summary.duration_ms,
        )
        return result
