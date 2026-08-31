import { githubMarkdownForDisplay } from "../src/pages/issueTriageModel.js";

function assertEqual(actual: string, expected: string): void {
  if (actual !== expected) throw new Error(`Expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

const githubImage = '<img width="211" alt="Image" src="https://github.com/user-attachments/assets/example" />';
assertEqual(
  githubMarkdownForDisplay(githubImage, "project one"),
  "[![Image](/api/projects/project%20one/issue-triage/attachment?url=https%3A%2F%2Fgithub.com%2Fuser-attachments%2Fassets%2Fexample)](https://github.com/user-attachments/assets/example)",
);

const unsafeImage = '<img alt="private" src="https://example.com/private.png" />';
assertEqual(githubMarkdownForDisplay(unsafeImage, "project one"), unsafeImage);

const markdownImage = "![capture](https://github.com/user-attachments/assets/example.png)";
assertEqual(
  githubMarkdownForDisplay(markdownImage, "project one"),
  "[![capture](/api/projects/project%20one/issue-triage/attachment?url=https%3A%2F%2Fgithub.com%2Fuser-attachments%2Fassets%2Fexample.png)](https://github.com/user-attachments/assets/example.png)",
);

console.log("issueTriageModel.test.ts OK");
