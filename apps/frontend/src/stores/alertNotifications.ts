import { defineStore } from "pinia";
import { computed, ref } from "vue";

import type { AlertNotification, AlertNotificationListResponse } from "@agent-py/api-contracts";

import { createApiClient } from "../api/apiClient";
import { AUTH_TOKEN_STORAGE_KEY } from "../authClient";
import { API_BASE_URL } from "../config";

export const useAlertNotificationsStore = defineStore("alert-notifications", () => {
  const items = ref<readonly AlertNotification[]>([]);
  const activeCount = computed(() => items.value.filter((item) => item.status === "firing").length);
  const api = createApiClient({
    baseUrl: API_BASE_URL,
    getAccessToken: () => window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  });
  let sequence = 0;

  async function refresh(): Promise<void> {
    const request = ++sequence;
    try {
      const result = await api.request<AlertNotificationListResponse>("/aiops/alerts/notifications");
      if (request === sequence) items.value = result.items;
    } catch {
      if (request === sequence) items.value = [];
    }
  }

  function reset(): void {
    sequence += 1;
    items.value = [];
  }

  return { items, activeCount, refresh, reset };
});
