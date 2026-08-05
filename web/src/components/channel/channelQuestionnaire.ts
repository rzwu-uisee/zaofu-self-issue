import type { ChannelDetail } from "../../api/types";

function questionRecords(
  value: ChannelDetail["open_questions"],
): Array<Record<string, unknown>> {
  if (Array.isArray(value)) return value;
  return value ? Object.values(value) : [];
}

export function openOwnerQuestionnaire(
  detail: ChannelDetail | null,
  threadId: string,
): Array<Record<string, unknown>> {
  const openIds = new Set(
    questionRecords(detail?.open_questions)
      .filter((item) => (
        String(item.thread_id || "main") === threadId
        && String(item.status || "") === "open"
      ))
      .map((item) => String(item.question_id || "").trim())
      .filter(Boolean),
  );
  return (detail?.owner_questionnaires?.[threadId] ?? [])
    .filter((item) => openIds.has(String(item.question_id || "").trim()));
}
