import { computed, ref } from "vue";
import { defineStore } from "pinia";

import type {
  ChatMessage,
  ChatAssemblyConfigurationResponse,
  ChatSessionSummary,
  ChatMemoryMode,
  ReferenceSourceSseEvent,
  ToolCallAudit
} from "@agent-py/api-contracts";

import { ApiClientError } from "../api/apiClient";
import { createChatClient, type ChatClient } from "../chat/chatClient";
import { useFeedbackStore } from "./feedback";
import { toUserFacingError } from "../ui/userFacingError";

export type ChatReference = ReferenceSourceSseEvent["reference"];

export interface LiveToolCall {
  readonly id: string;
  readonly name: string;
  readonly status: "started" | "delta" | "completed" | "failed";
  readonly input?: unknown;
  readonly output?: unknown;
}

let clientFactory: () => ChatClient = createChatClient;
export const CHAT_TYPEWRITER_DELAY_MS = 28;

export function setChatClientFactoryForTests(factory: (() => ChatClient) | null): void {
  clientFactory = factory ?? createChatClient;
}

export const useChatStore = defineStore("chat", () => {
  const client = clientFactory();
  const sessions = ref<readonly ChatSessionSummary[]>([]);
  const activeSessionId = ref<string | null>(null);
  const messages = ref<readonly ChatMessage[]>([]);
  const toolAudits = ref<readonly ToolCallAudit[]>([]);
  const liveToolCalls = ref<readonly LiveToolCall[]>([]);
  const references = ref<readonly ChatReference[]>([]);
  const isLoading = ref(false);
  const isLoadingOlder = ref(false);
  const nextBefore = ref<string | null>(null);
  let identityVersion = 0;
  let sessionLoadVersion = 0;
  let sending: AbortController | null = null;
  const isSending = ref(false);
  const isUpdatingMemory = ref(false);
  const errorMessage = ref<string | null>(null);
  const configuration = ref<ChatAssemblyConfigurationResponse | null>(null);
  const isSavingConfiguration = ref(false);
  const activeSession = computed(
    () => sessions.value.find((item) => item.id === activeSessionId.value) ?? null
  );

  function reportError(error: unknown): void {
    const message = toUserFacingError(error);
    errorMessage.value = message;
    useFeedbackStore().showError(message);
  }

  function setReferencesFromMessages(nextMessages: readonly ChatMessage[]): void {
    const latestAssistant = [...nextMessages]
      .reverse()
      .find((item) => item.role === "assistant");
    references.value = uniqueReferences(latestAssistant?.metadata.citations ?? []);
  }

  async function loadSession(sessionId: string, preserveHistory = false): Promise<void> {
    const version = ++sessionLoadVersion;
    const [detail, audits] = await Promise.all([
      client.getSession(sessionId),
      client.listToolCallAudits(sessionId)
    ]);
    if (version !== sessionLoadVersion) return;
    const firstId = detail.messages[0]?.id;
    const prefixLength = preserveHistory && activeSessionId.value === sessionId && detail.nextBefore
      ? messages.value.findIndex((message) => message.id === firstId)
      : -1;
    activeSessionId.value = detail.session.id;
    if (prefixLength > 0) {
      messages.value = [...messages.value.slice(0, prefixLength), ...detail.messages];
    } else {
      messages.value = detail.messages;
      nextBefore.value = detail.nextBefore ?? null;
    }
    toolAudits.value = audits.items;
    liveToolCalls.value = [];
    setReferencesFromMessages(detail.messages);
    upsertSession(detail.session);
  }

  function upsertSession(nextSession: ChatSessionSummary): void {
    sessions.value = [
      nextSession,
      ...sessions.value.filter((item) => item.id !== nextSession.id)
    ].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  }

  async function reloadSessions(): Promise<void> {
    const version = identityVersion;
    const result = await client.listSessions();
    if (version === identityVersion) sessions.value = result.items;
  }

  function reset(): void {
    identityVersion += 1;
    sessionLoadVersion += 1;
    sending?.abort();
    sending = null;
    sessions.value = [];
    activeSessionId.value = null;
    messages.value = [];
    toolAudits.value = [];
    liveToolCalls.value = [];
    references.value = [];
    errorMessage.value = null;
    configuration.value = null;
    isSavingConfiguration.value = false;
    isLoading.value = false;
    isLoadingOlder.value = false;
    nextBefore.value = null;
    isSending.value = false;
    isUpdatingMemory.value = false;
  }

  return {
    activeSessionId,
    activeSession,
    errorMessage,
    configuration,
    isSavingConfiguration,
    isLoading,
    isLoadingOlder,
    hasOlderMessages: computed(() => nextBefore.value !== null),
    isSending,
    isUpdatingMemory,
    liveToolCalls,
    messages,
    references,
    sessions,
    toolAudits,
    initialize: async (): Promise<void> => {
      const version = identityVersion;
      isLoading.value = true;
      errorMessage.value = null;
      try {
        const [loadedConfiguration] = await Promise.all([client.getConfiguration?.() ?? Promise.resolve(null), reloadSessions()]);
        if (version !== identityVersion) return;
        configuration.value = loadedConfiguration;
        const selected = sessions.value.find((item) => item.id === activeSessionId.value) ?? sessions.value[0];
        if (selected !== undefined) {
          await loadSession(selected.id);
        } else {
          messages.value = [];
          nextBefore.value = null;
          toolAudits.value = [];
          references.value = [];
        }
      } catch (error) {
        if (version !== identityVersion) return;
        reportError(error);
        throw error;
      } finally {
        if (version === identityVersion) isLoading.value = false;
      }
    },
    newSession: async (): Promise<ChatSessionSummary> => {
      const version = identityVersion;
      try {
        const created = await client.createSession({});
        if (version !== identityVersion) return created;
        sessionLoadVersion += 1;
        upsertSession(created);
        activeSessionId.value = created.id;
        messages.value = [];
        nextBefore.value = null;
        toolAudits.value = [];
        liveToolCalls.value = [];
        references.value = [];
        return created;
      } catch (error) {
        reportError(error);
        throw error;
      }
    },
    selectSession: async (sessionId: string): Promise<void> => {
      const version = identityVersion;
      isLoading.value = true;
      try {
        await loadSession(sessionId);
      } catch (error) {
        if (version !== identityVersion) return;
        reportError(error);
        throw error;
      } finally {
        if (version === identityVersion) isLoading.value = false;
      }
    },
    loadOlderMessages: async (): Promise<void> => {
      const sessionId = activeSessionId.value;
      const before = nextBefore.value;
      if (sessionId === null || before === null || isLoadingOlder.value || isLoading.value || isSending.value) return;
      const version = sessionLoadVersion;
      const currentIdentity = identityVersion;
      isLoadingOlder.value = true;
      try {
        const detail = await client.getSession(sessionId, before);
        if (version !== sessionLoadVersion || activeSessionId.value !== sessionId || isSending.value) return;
        const loadedIds = new Set(messages.value.map((message) => message.id));
        messages.value = [...detail.messages.filter((message) => !loadedIds.has(message.id)), ...messages.value];
        nextBefore.value = detail.nextBefore ?? null;
        setReferencesFromMessages(messages.value);
      } catch (error) {
        if (version !== sessionLoadVersion) return;
        reportError(error);
        throw error;
      } finally {
        if (currentIdentity === identityVersion) isLoadingOlder.value = false;
      }
    },
    deleteSession: async (sessionId: string): Promise<void> => {
      const version = identityVersion;
      try {
        await client.deleteSession(sessionId);
        if (version !== identityVersion) return;
        sessions.value = sessions.value.filter((item) => item.id !== sessionId);
        if (activeSessionId.value === sessionId) {
          const nextSession = sessions.value[0];
          if (nextSession === undefined) {
            sessionLoadVersion += 1;
            activeSessionId.value = null;
            messages.value = [];
            nextBefore.value = null;
            toolAudits.value = [];
            references.value = [];
          } else {
            await loadSession(nextSession.id);
          }
        }
      } catch (error) {
        if (version !== identityVersion) return;
        reportError(error);
        throw error;
      }
    },
    saveConfiguration: async (systemPromptId: string, skillIds: readonly string[]): Promise<void> => {
      isSavingConfiguration.value = true;
      try {
        if (client.updateConfiguration === undefined) return;
        configuration.value = await client.updateConfiguration({ systemPromptId, skillIds });
      } catch (error) {
        reportError(error);
        throw error;
      } finally {
        isSavingConfiguration.value = false;
      }
    },
    createPrompt: async (label: string, content: string): Promise<void> => {
      isSavingConfiguration.value = true;
      try {
        if (client.createPrompt === undefined || client.getConfiguration === undefined) return;
        await client.createPrompt({ label, content });
        configuration.value = await client.getConfiguration();
      } catch (error) {
        reportError(error);
        throw error;
      } finally {
        isSavingConfiguration.value = false;
      }
    },
    updatePrompt: async (promptId: string, label: string, content: string): Promise<void> => {
      isSavingConfiguration.value = true;
      try {
        if (client.updatePrompt === undefined || client.getConfiguration === undefined) return;
        await client.updatePrompt(promptId, { label, content });
        configuration.value = await client.getConfiguration();
      } catch (error) {
        reportError(error);
        throw error;
      } finally {
        isSavingConfiguration.value = false;
      }
    },
    deletePrompt: async (promptId: string): Promise<void> => {
      isSavingConfiguration.value = true;
      try {
        if (client.deletePrompt === undefined || client.getConfiguration === undefined) return;
        await client.deletePrompt(promptId);
        configuration.value = await client.getConfiguration();
      } catch (error) {
        reportError(error);
        throw error;
      } finally {
        isSavingConfiguration.value = false;
      }
    },
    uploadSkill: async (file: File): Promise<void> => {
      isSavingConfiguration.value = true;
      try {
        if (client.uploadSkill === undefined || client.getConfiguration === undefined) return;
        await client.uploadSkill(file);
        configuration.value = await client.getConfiguration();
      } catch (error) {
        reportError(error);
        throw error;
      } finally {
        isSavingConfiguration.value = false;
      }
    },
    deleteSkill: async (skillId: string): Promise<void> => {
      isSavingConfiguration.value = true;
      try {
        if (client.deleteSkill === undefined || client.getConfiguration === undefined) return;
        await client.deleteSkill(skillId);
        configuration.value = await client.getConfiguration();
      } catch (error) {
        reportError(error);
        throw error;
      } finally {
        isSavingConfiguration.value = false;
      }
    },
    reset,
    updateMemoryMode: async (mode: ChatMemoryMode): Promise<void> => {
      const sessionId = activeSessionId.value;
      if (sessionId === null || isUpdatingMemory.value) return;
      isUpdatingMemory.value = true;
      try {
        const updated = await client.updateMemoryMode(sessionId, mode);
        upsertSession(updated);
      } catch (error) {
        reportError(error);
        throw error;
      } finally {
        isUpdatingMemory.value = false;
      }
    },
    compactMemory: async (): Promise<void> => {
      const sessionId = activeSessionId.value;
      if (sessionId === null || isUpdatingMemory.value) return;
      isUpdatingMemory.value = true;
      try {
        const updated = await client.compactMemory(sessionId);
        upsertSession(updated);
      } catch (error) {
        reportError(error);
        throw error;
      } finally {
        isUpdatingMemory.value = false;
      }
    },
    send: async (rawContent: string): Promise<void> => {
      const content = rawContent.trim();
      if (content.length === 0 || isSending.value) {
        return;
      }
      if ((activeSession.value?.memory.contextUsagePercent ?? 0) >= 95) {
        reportError(new Error("上下文已达到 95%，请执行手动压缩后再继续对话。"));
        return;
      }
      const version = identityVersion;
      const controller = new AbortController();
      sending = controller;
      isSending.value = true;
      try {
        const targetSessionId = activeSessionId.value ?? (await createNewSession(client, (session) => {
          if (version === identityVersion) upsertSession(session);
        }));
        if (version !== identityVersion) return;
        if (activeSessionId.value === null) {
          activeSessionId.value = targetSessionId;
        }
        errorMessage.value = null;
        references.value = [];
        const optimisticUser = createOptimisticMessage(targetSessionId, "user", content);
        const draftId = `message_draft_${Date.now()}`;
        messages.value = [...messages.value, optimisticUser];
        let finished = false;
        for await (const event of client.streamMessage(targetSessionId, { content }, controller.signal)) {
          if (version !== identityVersion || activeSessionId.value !== targetSessionId) return;
          if (event.type === "content.delta") {
            for (const character of event.delta) {
              if (version !== identityVersion || activeSessionId.value !== targetSessionId) return;
              updateAssistantDraft(targetSessionId, draftId, character, messages);
              await waitForTypewriterTick();
            }
          }
          if (event.type === "reasoning.delta") {
            updateAssistantReasoning(targetSessionId, draftId, event.delta, messages);
          }
          if (event.type === "reference.source") {
            references.value = uniqueReferences([...references.value, event.reference]);
          }
          if (event.type === "tool.call") {
            updateLiveToolCall(event.toolCall, liveToolCalls);
          }
          if (event.type === "error") {
            throw new ApiClientError(event.error);
          }
          if (event.type === "complete") {
            finished = true;
          }
        }
        if (!finished) {
          throw new Error("回答流在完成前意外中断。");
        }
        if (version === identityVersion && activeSessionId.value === targetSessionId) {
          await Promise.all([loadSession(targetSessionId, true), reloadSessions()]);
        }
      } catch (error) {
        if (version !== identityVersion) return;
        messages.value = messages.value.filter((item) => !item.id.startsWith("message_draft_"));
        reportError(error);
        throw error;
      } finally {
        if (sending === controller) {
          sending = null;
          isSending.value = false;
          liveToolCalls.value = [];
        }
      }
    }
  };
});

async function createNewSession(
  client: ChatClient,
  upsert: (session: ChatSessionSummary) => void
): Promise<string> {
  const session = await client.createSession({});
  upsert(session);
  return session.id;
}

function createOptimisticMessage(
  sessionId: string,
  role: ChatMessage["role"],
  content: string
): ChatMessage {
  return {
    id: `message_optimistic_${Date.now()}`,
    ownerUserId: "current",
    sessionId,
    role,
    content,
    metadata: {},
    createdAt: new Date().toISOString()
  };
}

function updateAssistantDraft(
  sessionId: string,
  draftId: string,
  delta: string,
  target: { value: readonly ChatMessage[] }
): void {
  const existing = target.value.find((item) => item.id === draftId);
  const draft: ChatMessage = {
    id: draftId,
    ownerUserId: "current",
    sessionId,
    role: "assistant",
    content: `${existing?.content ?? ""}${delta}`,
    metadata: { citations: [] },
    createdAt: existing?.createdAt ?? new Date().toISOString()
  };
  target.value = existing === undefined
    ? [...target.value, draft]
    : target.value.map((item) => (item.id === draftId ? draft : item));
}

function updateAssistantReasoning(
  sessionId: string,
  draftId: string,
  delta: string,
  target: { value: readonly ChatMessage[] }
): void {
  const existing = target.value.find((item) => item.id === draftId);
  const draft: ChatMessage = {
    id: draftId,
    ownerUserId: "current",
    sessionId,
    role: "assistant",
    content: existing?.content ?? "",
    metadata: {
      ...existing?.metadata,
      citations: existing?.metadata.citations ?? [],
      reasoning: [...(existing?.metadata.reasoning ?? []), delta]
    },
    createdAt: existing?.createdAt ?? new Date().toISOString()
  };
  target.value = existing === undefined
    ? [...target.value, draft]
    : target.value.map((item) => (item.id === draftId ? draft : item));
}

function updateLiveToolCall(
  next: LiveToolCall,
  target: { value: readonly LiveToolCall[] }
): void {
  const existing = target.value.find((item) => item.id === next.id);
  const merged: LiveToolCall = {
    ...existing,
    ...next,
    ...(next.input === undefined ? {} : { input: next.input }),
    ...(next.output === undefined ? {} : { output: next.output })
  };
  target.value = [merged, ...target.value.filter((item) => item.id !== next.id)];
}

function uniqueReferences(items: readonly ChatReference[]): readonly ChatReference[] {
  const seen = new Set<string>();
  return items
    .filter((item) => {
      if (seen.has(item.id)) {
        return false;
      }
      seen.add(item.id);
      return true;
    })
    .sort((left, right) => referenceScore(right) - referenceScore(left))
    .slice(0, 5);
}

function referenceScore(reference: ChatReference): number {
  return reference.rerankScore ?? reference.score ?? Number.NEGATIVE_INFINITY;
}

function waitForTypewriterTick(): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, CHAT_TYPEWRITER_DELAY_MS);
  });
}
