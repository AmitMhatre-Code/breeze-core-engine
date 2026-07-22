import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // Terminal Pro design language forbids native checkbox chrome (see
    // docs/ui-revamp/DESIGN_LANGUAGE.md §5). Route everyone through the
    // shared, themed Checkbox component instead.
    files: ["src/**/*.tsx"],
    ignores: ["src/components/ui/Checkbox.tsx"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "JSXOpeningElement[name.name='input'] > JSXAttribute[name.name='type'][value.value='checkbox']",
          message:
            'Do not render a native <input type="checkbox">. Use <Checkbox /> from "@/components/ui/Checkbox" so it gets the Terminal Pro flat-square styling instead of native OS chrome.',
        },
      ],
    },
  },
]);

export default eslintConfig;
