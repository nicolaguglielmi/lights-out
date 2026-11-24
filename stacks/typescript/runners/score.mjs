// SPDX-License-Identifier: Apache-2.0
//
// Score a StrykerJS JSON report (docs/adr/0042 §1, docs/adr/0102), node only:
// the typescript stack's scorer, copied beside mutation.sh. Prints one JSON
// line to stdout: {"total": N, "killed": K, "survived": S, "score": <K/N, or
// null when N is 0>}, and every survivor to stderr as "SURVIVOR file:line
// mutator".
//
// The population is Stryker's "valid" set: detected = Killed + Timeout,
// undetected = Survived + NoCoverage, score = detected / valid. RuntimeError,
// CompileError, Ignored and Pending are excluded.
import { readFileSync } from "node:fs";

const reportPath = process.argv[2];
if (!reportPath) {
  console.error("usage: node score.mjs <mutation.json>");
  process.exit(2);
}
const report = JSON.parse(readFileSync(reportPath, "utf8"));

let killed = 0;
let survived = 0;
let excluded = 0;
const survivors = [];
for (const [file, data] of Object.entries(report.files ?? {})) {
  for (const mutant of data.mutants ?? []) {
    switch (mutant.status) {
      case "Killed":
      case "Timeout":
        killed += 1;
        break;
      case "Survived":
      case "NoCoverage":
        survived += 1;
        survivors.push(`SURVIVOR ${file}:${mutant.location?.start?.line ?? "?"} ${mutant.mutatorName}`);
        break;
      default:
        excluded += 1;
    }
  }
}

const total = killed + survived;
const score = total > 0 ? killed / total : null;
console.log(JSON.stringify({ total, killed, survived, score }));
for (const line of survivors) console.error(line);
console.error(`score: ${excluded} mutant(s) excluded from the population (RuntimeError, CompileError, Ignored, Pending)`);
