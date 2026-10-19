// @ts-check
// Prints whether each path argument is on the deny list in STEPSWITCH_DENY_RULES, relative to
// the current directory: the cross-check of the Python and JavaScript matchers
// (tests/unit/test_adapter_extensions.py).

import { deniedPath, denyRules } from "../core/stepswitch.js";

const rules = denyRules(process.env);
const paths = process.argv.slice(2);
console.log(JSON.stringify(paths.map((target) => deniedPath(rules, process.cwd(), target))));
