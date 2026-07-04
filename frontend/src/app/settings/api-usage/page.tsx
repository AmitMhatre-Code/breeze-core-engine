import { redirect } from "next/navigation";

export default function ApiUsageRedirectPage() {
  redirect("/settings");
}
