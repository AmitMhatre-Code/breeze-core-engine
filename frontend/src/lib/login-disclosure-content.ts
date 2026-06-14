const TERMS_BULLET_PATTERN =
  /^(\* You also confirm that you agree to, and are bound by, the )\[Breeze Modern Terms & Conditions\]\(\/terms-and-conditions\)( governing the use of this application\.)$/m;

type ComposeOptions = {
  expandTerms: boolean;
  termsMd?: string;
};

export function composeDisclosureMarkdown(
  disclosureMd: string,
  { expandTerms, termsMd = "" }: ComposeOptions,
): string {
  if (!expandTerms || !termsMd.trim()) {
    return disclosureMd;
  }

  const match = disclosureMd.match(TERMS_BULLET_PATTERN);
  if (!match) {
    return disclosureMd;
  }

  const replacement = `${match[1]}**Breeze Modern Terms & Conditions**${match[2]}\n\n---\n\n${termsMd.trim()}`;
  return disclosureMd.replace(TERMS_BULLET_PATTERN, replacement);
}
