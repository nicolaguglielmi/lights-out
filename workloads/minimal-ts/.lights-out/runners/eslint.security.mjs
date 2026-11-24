// SPDX-License-Identifier: Apache-2.0
//
// The flat config sast.sh runs eslint with (docs/adr/0102): the security
// plugin's recommended rules over every .ts, .tsx, .mts and .cts file the
// CLI is pointed at, parsed with @typescript-eslint/parser -- style is not
// a gate. Copied into the tools' prefix at run time so dependencies resolve
// from there, not from the repository.
import tsParser from "@typescript-eslint/parser";
import security from "eslint-plugin-security";

export default [
  {
    ...security.configs.recommended,
    files: ["**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts"],
    ignores: ["**/*.d.ts"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        console: "readonly",
        process: "readonly",
        require: "readonly",
        module: "writable",
        __dirname: "readonly",
      },
    },
  },
];
