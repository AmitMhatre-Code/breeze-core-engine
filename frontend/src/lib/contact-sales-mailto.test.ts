import { describe, expect, it } from "vitest";
import {
  buildContactSalesMailto,
  buildContactSalesMailtoBody,
  buildContactSalesMailtoForLicenseStatus,
} from "@/lib/contact-sales-mailto";

describe("contact-sales-mailto", () => {
  const fullParams = {
    status: "expired" as const,
    licenseId: 42,
    deploymentName: "my-stack",
    label: "paper",
    licenseKey: "key-abc-123",
    createdAt: "2025-01-01T00:00:00Z",
    expiresAt: "2025-01-15T00:00:00Z",
    revokedAt: null,
    deploymentStatus: "deployed_successfully",
    publicIp: "203.0.113.10",
    accountEmail: "user@example.com",
    deploymentOrigin: "http://203.0.113.10",
    appVersion: "1.4.2",
  };

  it("builds subject and body with license fields", () => {
    const body = buildContactSalesMailtoBody(fullParams);
    expect(body).toContain("Status: Expired");
    expect(body).toContain("License ID: 42");
    expect(body).toContain("Deployment name: my-stack");
    expect(body).toContain("License key: key-abc-123");
    expect(body).toContain("Public IP: 203.0.113.10");
    expect(body).toContain("Account email: user@example.com");
    expect(body).toContain("App version: 1.4.2");
  });

  it("encodes mailto URL", () => {
    const href = buildContactSalesMailto("sales@breeze-ui.com", fullParams);
    expect(href.startsWith("mailto:sales%40breeze-ui.com?")).toBe(true);
    expect(href).toContain("subject=");
    expect(href).toContain("body=");
    const decoded = decodeURIComponent(href);
    expect(decoded).toContain("Breeze license assistance — my-stack (Expired)");
    expect(decoded).toContain("License ID: 42");
  });

  it("returns null when sales email env is unset", () => {
    const prev = process.env.NEXT_PUBLIC_SALES_EMAIL;
    delete process.env.NEXT_PUBLIC_SALES_EMAIL;
    expect(
      buildContactSalesMailtoForLicenseStatus("revoked", {
        license_key: "x",
        public_ip: "1.2.3.4",
      }),
    ).toBeNull();
    process.env.NEXT_PUBLIC_SALES_EMAIL = prev;
  });

  it("builds mailto from API contact_sales snapshot", () => {
    process.env.NEXT_PUBLIC_SALES_EMAIL = "sales@breeze-ui.com";
    const href = buildContactSalesMailtoForLicenseStatus("expired", {
      license_key: "deploy-key",
      public_ip: "203.0.113.10",
      deployment_origin: "http://203.0.113.10",
      app_version: "1.4.0",
    });
    expect(href).not.toBeNull();
    const decoded = decodeURIComponent(href!);
    expect(decoded).toContain("License key: deploy-key");
    expect(decoded).toContain("Public IP: 203.0.113.10");
  });
});
