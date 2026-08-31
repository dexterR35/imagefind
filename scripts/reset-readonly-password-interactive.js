// Adapt the pipe-oriented helper to complete after one terminal input line.
const fs = require("node:fs");
const path = require("node:path");

const helperPath = path.join(__dirname, "reset-readonly-password.js");
let source = fs.readFileSync(helperPath, "utf8");
source = source.replace('process.stdin.on("end", () => {', 'process.stdin.once("data", () => {');
source = source.replace(
  'console.log("Shared password updated and persistent sessions revoked.");',
  'console.log("Shared password updated and persistent sessions revoked."); process.exitCode = 0; process.stdin.pause();',
);
new Function("require", "__dirname", "process", source)(require, __dirname, process);
