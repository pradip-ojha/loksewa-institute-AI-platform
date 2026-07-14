import { useCallback, useEffect, useRef, useState } from "react";
import {
  skillLayerService,
  type ChatMessage,
  type Draft,
  type SkillDetail,
  type SkillListItem,
} from "../../services/skillLayer";
import { getErrorMessage } from "../../utils/error";
import { SlidersHorizontal } from "lucide-react";
import { PageHeader, Button, Alert, StatusBadge } from "../../components/ui";

export function AdminSkillLayer() {
  const [skills, setSkills] = useState<SkillListItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Chat state
  const [chatId, setChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [approving, setApproving] = useState(false);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  const loadSkills = useCallback(async () => {
    setLoadingList(true);
    try {
      const data = await skillLayerService.listSkills();
      setSkills(data);
      setSelected((prev) => prev ?? data[0]?.agent_type ?? null);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load skills."));
    } finally {
      setLoadingList(false);
    }
  }, []);

  const loadDetail = useCallback(async (agentType: string) => {
    try {
      setDetail(await skillLayerService.getSkillDetail(agentType));
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load skill detail."));
    }
  }, []);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  useEffect(() => {
    if (!selected) return;
    loadDetail(selected);
    // Reset chat when switching agents
    setChatId(null);
    setMessages([]);
    setDraft(null);
    setInput("");
    setError(null);
  }, [selected, loadDetail]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const ensureChat = useCallback(async (): Promise<string> => {
    if (chatId) return chatId;
    const started = await skillLayerService.startChat(selected!);
    setChatId(started.chat_id);
    setMessages(started.messages);
    return started.chat_id;
  }, [chatId, selected]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending || !selected) return;
    setSending(true);
    setError(null);
    setInput("");
    setMessages((prev) => [...prev, { role: "admin", content: text }]);
    try {
      const id = await ensureChat();
      const res = await skillLayerService.sendMessage(id, text);
      setMessages((prev) => [...prev, { role: "assistant", content: res.reply }]);
      setDraft(res.draft);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to send message."));
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Failed to get a response. Please try again." },
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleApprove = async () => {
    if (!chatId || !draft || approving) return;
    setApproving(true);
    setError(null);
    try {
      await skillLayerService.approveChat(chatId);
      // New version is now active — refresh everything and reset the chat.
      setDraft(null);
      setChatId(null);
      setMessages([]);
      await loadDetail(selected!);
      await loadSkills();
    } catch (err) {
      setError(getErrorMessage(err, "Failed to approve the skill update."));
    } finally {
      setApproving(false);
    }
  };

  const handleDiscard = async () => {
    if (!chatId) return;
    setError(null);
    try {
      await skillLayerService.discardChat(chatId);
    } catch {
      /* discard is best-effort */
    }
    setDraft(null);
    setChatId(null);
    setMessages([]);
  };

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col">
      <PageHeader
        title="Skill Layer"
        description="Improve an AI agent's behavior by chatting with the Skill Builder, then approve to activate a new version."
        icon={<SlidersHorizontal className="h-5 w-5" />}
      />

      {error && <Alert className="mb-3">{error}</Alert>}

      <div className="grid min-h-0 flex-1 grid-cols-12 gap-4">
        {/* Left: agent selector */}
        <div className="col-span-3 min-h-0 overflow-y-auto rounded-lg border border-gray-200 bg-white">
          <div className="border-b border-gray-200 px-4 py-3 text-xs font-medium uppercase tracking-wider text-gray-400">
            Agents
          </div>
          {loadingList ? (
            <div className="p-4 text-sm text-gray-400">Loading…</div>
          ) : (
            <ul>
              {skills.map((s) => (
                <li key={s.agent_type}>
                  <button
                    onClick={() => setSelected(s.agent_type)}
                    className={`flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left text-sm transition-colors ${
                      selected === s.agent_type
                        ? "bg-brand-50 font-medium text-brand-700"
                        : "text-gray-700 hover:bg-gray-50"
                    }`}
                  >
                    <span className="truncate">{s.agent_type}</span>
                    {s.active_version_number != null && (
                      <span className="flex-shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
                        v{s.active_version_number}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Center: chat */}
        <div className="col-span-5 flex min-h-0 flex-col rounded-lg border border-gray-200 bg-white">
          <div className="border-b border-gray-200 px-4 py-3 text-sm font-semibold text-gray-900">
            Skill Builder Chat
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            {messages.length === 0 ? (
              <p className="text-sm text-gray-400">
                Describe a change you'd like for <strong>{selected}</strong> to start a conversation.
              </p>
            ) : (
              messages.map((m, i) => (
                <div
                  key={i}
                  className={`flex ${m.role === "admin" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3.5 py-2 text-sm ${
                      m.role === "admin"
                        ? "bg-brand-600 text-white"
                        : "bg-gray-100 text-gray-800"
                    }`}
                  >
                    {m.content}
                  </div>
                </div>
              ))
            )}
            {sending && <div className="text-xs text-gray-400">Skill Builder is thinking…</div>}
            <div ref={chatEndRef} />
          </div>
          <div className="border-t border-gray-200 p-3">
            <div className="flex gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                rows={2}
                placeholder="e.g. Make questions harder and add more numerical reasoning…"
                disabled={!selected || sending}
                className="flex-1 resize-none rounded-md border border-gray-300 px-3 py-2 text-sm transition-colors focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20"
              />
              <Button className="self-end" onClick={handleSend} disabled={!input.trim() || sending || !selected}>
                Send
              </Button>
            </div>
          </div>
        </div>

        {/* Right: current / draft / history */}
        <div className="col-span-4 min-h-0 space-y-4 overflow-y-auto">
          {/* Draft / approve */}
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="mb-2 text-xs font-medium uppercase tracking-wider text-gray-400">
              Proposed Draft
            </div>
            {draft ? (
              <>
                <p className="mb-2 text-xs text-gray-500">{draft.change_summary}</p>
                <div className="mb-3 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md border border-warning-100 bg-warning-50 p-3 text-sm text-gray-800">
                  {draft.instruction_text}
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={handleApprove} loading={approving}>
                    Approve &amp; Activate
                  </Button>
                  <Button size="sm" variant="secondary" onClick={handleDiscard} disabled={approving}>
                    Discard
                  </Button>
                </div>
              </>
            ) : (
              <p className="text-sm text-gray-400">
                No proposed change yet. Ask the Skill Builder for a change and a draft will appear here.
              </p>
            )}
          </div>

          {/* Current active */}
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium uppercase tracking-wider text-gray-400">
                Active Instruction
              </span>
              {detail?.active && (
                <span className="text-xs font-medium text-gray-500">v{detail.active.version_number}</span>
              )}
            </div>
            <div className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md bg-gray-50 p-3 text-sm text-gray-800">
              {detail?.active?.instruction_text || "No active skill."}
            </div>
          </div>

          {/* Version history */}
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="mb-2 text-xs font-medium uppercase tracking-wider text-gray-400">
              Version History
            </div>
            {detail && detail.history.length > 0 ? (
              <ul className="space-y-2">
                {detail.history.map((v) => (
                  <li key={v.id} className="rounded-md border border-gray-200 px-3 py-2 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-gray-800">v{v.version_number}</span>
                      <StatusBadge status={v.status} />
                    </div>
                    {v.change_summary && (
                      <p className="mt-1 text-xs text-gray-500">{v.change_summary}</p>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-400">No versions yet.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
