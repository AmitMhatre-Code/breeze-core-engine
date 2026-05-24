import type { DeploymentLicenseStatus } from "@/lib/deployment-license";
import type { DeploymentLicenseContactSales } from "@/lib/deployment-license-status";

export type ContactSalesLicenseParams = {
  status: DeploymentLicenseStatus;
  licenseId?: number;
  deploymentName?: string | null;
  label?: string | null;
  licenseKey?: string | null;
  createdAt?: string | null;
  expiresAt?: string | null;
  revokedAt?: string | null;
  deploymentStatus?: string | null;
  publicIp?: string | null;
  accountEmail?: string | null;
  deploymentOrigin?: string | null;
  appVersion?: string | null;
};

const IST_TIME_ZONE = "Asia/Kolkata";
const IST_LOCALE = "en-IN";

function parseApiDateTime(raw: string): Date | null {
  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDisplayTimestamp(raw: string | null | undefined): string {
  if (!raw) return "—";
  try {
    const d = parseApiDateTime(raw);
    if (!d) return raw;
    return `${d.toLocaleString(IST_LOCALE, {
      timeZone: IST_TIME_ZONE,
      dateStyle: "medium",
      timeStyle: "short",
    })} IST`;
  } catch {
    return raw;
  }
}

export function getSalesEmail(): string | null {
  const email = process.env.NEXT_PUBLIC_SALES_EMAIL?.trim();
  return email || null;
}

function displayLabel(params: ContactSalesLicenseParams): string {
  const name = (params.deploymentName || params.label || "").trim();
  if (name) return name;
  if (params.licenseId != null) return `License #${params.licenseId}`;
  return "Deployment";
}

function statusLabel(status: DeploymentLicenseStatus): string {
  return status === "expired" ? "Expired" : "Revoked";
}

function appendLine(lines: string[], label: string, value: string | null | undefined): void {
  const text = (value ?? "").trim();
  if (text) lines.push(`${label}: ${text}`);
}

export function buildContactSalesMailtoBody(
  params: ContactSalesLicenseParams,
): string {
  const lines: string[] = [
    "Hello Breeze Sales,",
    "",
    "Please help with my license.",
    "",
    "--- License details ---",
    `Status: ${statusLabel(params.status)}`,
  ];

  if (params.licenseId != null) lines.push(`License ID: ${params.licenseId}`);
  appendLine(lines, "Deployment name", params.deploymentName);
  appendLine(lines, "Label", params.label);
  appendLine(lines, "License key", params.licenseKey);
  lines.push(`Created: ${formatDisplayTimestamp(params.createdAt)}`);
  lines.push(`Expires: ${formatDisplayTimestamp(params.expiresAt)}`);
  lines.push(`Revoked: ${formatDisplayTimestamp(params.revokedAt)}`);
  appendLine(lines, "Deployment status", params.deploymentStatus);
  appendLine(lines, "Public IP", params.publicIp);
  appendLine(lines, "Account email", params.accountEmail);
  appendLine(lines, "Deployment URL", params.deploymentOrigin);
  appendLine(lines, "App version", params.appVersion);
  lines.push("", "Thank you.");

  return lines.join("\n");
}

export function buildContactSalesMailto(
  salesEmail: string,
  params: ContactSalesLicenseParams,
): string {
  const to = salesEmail.trim();
  const subject = `Breeze license assistance — ${displayLabel(params)} (${statusLabel(params.status)})`;
  const body = buildContactSalesMailtoBody(params);
  return `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

export function contactSalesParamsFromApi(
  status: DeploymentLicenseStatus,
  contact: DeploymentLicenseContactSales | null | undefined,
): ContactSalesLicenseParams {
  return {
    status,
    licenseKey: contact?.license_key ?? null,
    publicIp: contact?.public_ip ?? null,
    deploymentOrigin: contact?.deployment_origin ?? null,
    appVersion: contact?.app_version ?? null,
  };
}

export function buildContactSalesMailtoForLicenseStatus(
  status: DeploymentLicenseStatus | null | undefined,
  contact: DeploymentLicenseContactSales | null | undefined,
): string | null {
  if (status !== "expired" && status !== "revoked") return null;
  const salesEmail = getSalesEmail();
  if (!salesEmail) return null;
  return buildContactSalesMailto(
    salesEmail,
    contactSalesParamsFromApi(status, contact),
  );
}
