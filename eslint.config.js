import globals from "globals";

/** Flat ESLint config for plain ES-module frontend JS (no framework plugins). */
export default [
  {
    ignores: [
      "web/static/js/lib/**",
      "**/node_modules/**",
      "web/.venv/**",
    ],
  },
  {
    files: ["web/static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.es2024,
      },
    },
    rules: {
      "no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "no-undef": "error",
      "no-redeclare": "error",
    },
  },
  {
    files: ["web/static/sw.js"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "script",
      globals: {
        ...globals.serviceworker,
        ...globals.es2024,
      },
    },
    rules: {
      "no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "no-undef": "error",
      "no-redeclare": "error",
    },
  },
];
