import { describe, expect, it } from "vitest";
import { composeDisclosureMarkdown } from "@/lib/login-disclosure-content";

const DISCLOSURE = `### Platform Acknowledgements
* By clicking "Proceed", you confirm that you have read and understood the SEBI regulatory risk disclosures regarding derivatives trading above.
* You also confirm that you agree to, and are bound by, the [Breeze Modern Terms & Conditions](/terms-and-conditions) governing the use of this application.`;

describe("composeDisclosureMarkdown", () => {
  it("returns disclosure unchanged when expandTerms is false", () => {
    expect(
      composeDisclosureMarkdown(DISCLOSURE, {
        expandTerms: false,
        termsMd: "# Full terms",
      }),
    ).toBe(DISCLOSURE);
  });

  it("inlines full terms when expandTerms is true", () => {
    const result = composeDisclosureMarkdown(DISCLOSURE, {
      expandTerms: true,
      termsMd: "# Terms body\n\nSection 1.",
    });
    expect(result).toContain("**Breeze Modern Terms & Conditions**");
    expect(result).not.toContain("[Breeze Modern Terms & Conditions](/terms-and-conditions)");
    expect(result).toContain("# Terms body");
    expect(result).toContain("Section 1.");
  });

  it("returns disclosure unchanged when terms markdown is empty", () => {
    expect(
      composeDisclosureMarkdown(DISCLOSURE, {
        expandTerms: true,
        termsMd: "",
      }),
    ).toBe(DISCLOSURE);
  });

  it("returns disclosure unchanged when T&C bullet is missing", () => {
    const withoutBullet = "### Platform Acknowledgements\n* First bullet only.";
    expect(
      composeDisclosureMarkdown(withoutBullet, {
        expandTerms: true,
        termsMd: "# Terms",
      }),
    ).toBe(withoutBullet);
  });
});
