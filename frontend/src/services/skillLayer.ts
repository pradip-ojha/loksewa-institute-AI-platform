import api from "./api";

export interface SkillListItem {
  agent_type: string;
  active_version_number: number | null;
  instruction_text: string;
  activated_at: string | null;
}

export interface SkillVersion {
  id: string;
  version_number: number;
  instruction_text: string;
  status: string;
  change_summary: string | null;
  created_at: string;
  activated_at: string | null;
}

export interface SkillDetail {
  agent_type: string;
  active: SkillVersion | null;
  history: SkillVersion[];
}

export interface ChatMessage {
  role: string;
  content: string;
}

export interface ChatStart {
  chat_id: string;
  agent_type: string;
  messages: ChatMessage[];
}

export interface Draft {
  version_id: string;
  version_number: number;
  instruction_text: string;
  change_summary: string;
}

export interface ChatReply {
  chat_id: string;
  reply: string;
  draft: Draft | null;
}

export interface ApproveResult {
  agent_type: string;
  active: SkillVersion | null;
}

export const skillLayerService = {
  listSkills: (): Promise<SkillListItem[]> =>
    api.get("/api/admin/skills").then((r) => r.data),

  getSkillDetail: (agentType: string): Promise<SkillDetail> =>
    api.get(`/api/admin/skills/${agentType}`).then((r) => r.data),

  startChat: (agentType: string): Promise<ChatStart> =>
    api.post("/api/admin/skills/chat/start", { agent_type: agentType }).then((r) => r.data),

  sendMessage: (chatId: string, message: string): Promise<ChatReply> =>
    api.post(`/api/admin/skills/chat/${chatId}/message`, { message }).then((r) => r.data),

  approveChat: (chatId: string): Promise<ApproveResult> =>
    api.post(`/api/admin/skills/chat/${chatId}/approve`).then((r) => r.data),

  discardChat: (chatId: string): Promise<void> =>
    api.post(`/api/admin/skills/chat/${chatId}/discard`).then(() => undefined),
};
