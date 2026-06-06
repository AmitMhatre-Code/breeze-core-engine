const ICICI_HANDOFF_CONSOLE_BASE = "https://breeze-ui.com/console/icici-handoff";
const ICICI_STATIC_IP_HANDOFF_PATH = "/static-ip";
const BREEZE_USER_HANDOFF_PATH = "/user-registration";

const _IPV4_RE = /^\d{1,3}(\.\d{1,3}){3}$/;

export function isDeploymentPublicIpv4(value: string): boolean {
  return _IPV4_RE.test(value.trim());
}

export function deploymentPublicIpFromWindow(): string | null {
  if (typeof window === "undefined") return null;
  const host = window.location.hostname.trim();
  return isDeploymentPublicIpv4(host) ? host : null;
}

function buildHandoffGuideUrl(publicIp: string, path: string): string {
  const ip = publicIp.trim();
  return `${ICICI_HANDOFF_CONSOLE_BASE}${path}?ip=${encodeURIComponent(ip)}`;
}

export function buildIciciStaticIpHandoffGuideUrl(publicIp: string): string {
  return buildHandoffGuideUrl(publicIp, ICICI_STATIC_IP_HANDOFF_PATH);
}

export function buildBreezeUserHandoffGuideUrl(publicIp: string): string {
  return buildHandoffGuideUrl(publicIp, BREEZE_USER_HANDOFF_PATH);
}

/** @deprecated Use buildIciciStaticIpHandoffGuideUrl — kept for register page and backend session compat. */
export function buildIciciHandoffGuideUrl(publicIp: string): string {
  return buildIciciStaticIpHandoffGuideUrl(publicIp);
}

export function iciciStaticIpHandoffUrlForCurrentDeployment(): string | null {
  const ip = deploymentPublicIpFromWindow();
  return ip ? buildIciciStaticIpHandoffGuideUrl(ip) : null;
}

export function breezeUserHandoffUrlForCurrentDeployment(): string | null {
  const ip = deploymentPublicIpFromWindow();
  return ip ? buildBreezeUserHandoffGuideUrl(ip) : null;
}

/** @deprecated Use iciciStaticIpHandoffUrlForCurrentDeployment — kept for register page compat. */
export function iciciHandoffGuideUrlForCurrentDeployment(): string | null {
  return iciciStaticIpHandoffUrlForCurrentDeployment();
}
