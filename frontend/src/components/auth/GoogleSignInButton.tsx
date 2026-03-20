/**
 * Traditional “Sign in with Google” control (light: white fill; dark: filled dark surface per Google dark-theme pattern).
 * Renders as a link; use full width on the parent via className.
 */
export function GoogleSignInButton({
  href,
  children = "Sign in with Google",
  className = "",
}: {
  href: string;
  children?: string;
  className?: string;
}) {
  return (
    <a
      href={href}
      className={[
        "inline-flex h-11 w-full items-center justify-center gap-3 rounded border border-[#dadce0] bg-white px-3 text-sm font-medium text-[#3c4043] shadow-sm transition hover:shadow-md active:bg-[#f8f9fa]",
        "dark:border-[#8e918f] dark:bg-[#131314] dark:text-[#e8eaed] dark:shadow-[0_1px_2px_0_rgba(0,0,0,0.45)] dark:hover:bg-[#1f1f1f] dark:hover:shadow-md dark:active:bg-[#2c2c2c]",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <GoogleGMark className="h-[18px] w-[18px] shrink-0" aria-hidden />
      <span className="font-medium tracking-[0.25px]">{children}</span>
    </a>
  );
}

function GoogleGMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden>
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      />
    </svg>
  );
}
