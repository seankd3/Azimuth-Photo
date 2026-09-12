import globals from "globals";

/** Flat ESLint config: the V2 UI modules, and the node specs that refute their kit. */
export default [
  {
    ignores: ["**/node_modules/**", "web/.venv/**", "build/**", "dist/**"],
  },
  {
    files: ["web/static/v2/**/*.js"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.es2024 },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" }],
      "no-undef": "error",
      "no-redeclare": "error",
      // A local that shadows an import once hid a kit helper behind a string
      // and killed every view; the linter refuses it now.
      "no-shadow": "error",
    },
  },
  {
    files: ["web/*.mjs", "scripts/*.mjs"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      globals: { ...globals.node, ...globals.es2024 },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" }],
      "no-undef": "error",
      "no-redeclare": "error",
    },
  },
];
