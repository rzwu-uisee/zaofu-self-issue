function prettyStandaloneJson(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed || (trimmed[0] !== "{" && trimmed[0] !== "[")) return null;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return null;
  }
}

export function prettyPrintIfJson(value: string): string {
  return prettyStandaloneJson(value) ?? value;
}

export function standaloneJsonMarkdown(value: string): string {
  const pretty = prettyStandaloneJson(value);
  if (pretty === null) return value;
  const longestBacktickRun = Math.max(
    0,
    ...[...pretty.matchAll(/`+/g)].map((match) => match[0].length),
  );
  const fence = "`".repeat(Math.max(3, longestBacktickRun + 1));
  return `${fence}json\n${pretty}\n${fence}`;
}
