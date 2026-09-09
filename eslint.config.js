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
    },
  },
  {
    files: ["web/*.mjs"],
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
