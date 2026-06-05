const ICICI_HANDOFF_CONSOLE_URL = "https://breeze-ui.com/console/icici-handoff";

const _IPV4_RE = /^\d{1,3}(\.\d{1,3}){3}$/;

export function isDeploymentPublicIpv4(value: string): boolean {
  return _IPV4_RE.test(value.trim());
}

export function deploymentPublicIpFromWindow(): string | null {
  if (typeof window === "undefined") return null;
  const host = window.location.hostname.trim();
  return isDeploymentPublicIpv4(host) ? host : null;
}

export function buildIciciHandoffGuideUrl(publicIp: string): string {
  const ip = publicIp.trim();
  return `${ICICI_HANDOFF_CONSOLE_URL}?ip=${encodeURIComponent(ip)}`;
}

export function iciciHandoffGuideUrlForCurrentDeployment(): string | null {
  const ip = deploymentPublicIpFromWindow();
  return ip ? buildIciciHandoffGuideUrl(ip) : null;
}
