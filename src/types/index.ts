export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export interface TerraformModule { id: string; provider: "AWS" | "Azure" | "Google Cloud"; namespace: string; name: string; version: string; description: string; riskScore: number; severity: Severity; findingCount: number; lastScan: string; downloads: string; stars: string; breakdown: { supplyChain: number; misconfiguration: number; secrets: number } }
export interface Finding { id: string; moduleId: string; severity: Severity; title: string; category: "Misconfiguration" | "Supply Chain" | "Secrets"; resource: string; file: string; line: number; evidence: string; recommendation: string; description: string }
export interface Scan { id: string; moduleId: string; version: string; status: "Completed"; findings: number; riskScore: number; severity: Severity; time: string; duration: string; trigger: "Manual" | "Scheduled" }
export interface SupplyChainEvent { date: string; title: string; description: string; warning?: boolean }
