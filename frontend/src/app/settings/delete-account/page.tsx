import { redirect } from "next/navigation";

export default function DeleteAccountRedirectPage() {
  redirect("/settings");
}
