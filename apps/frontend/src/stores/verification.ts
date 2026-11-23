import { defineStore } from "pinia";
import { ref } from "vue";

import type { VerificationPlan, VerificationPlanListResponse } from "@agent-py/api-contracts";

import { createApiClient } from "../api/apiClient";
import { AUTH_TOKEN_STORAGE_KEY } from "../authClient";
import { API_BASE_URL } from "../config";
import { toUserFacingError } from "../ui/userFacingError";

export const useVerificationStore = defineStore("verification", () => {
  const plans = ref<readonly VerificationPlan[]>([]);
  const isSaving = ref(false);
  const errorMessage = ref<string | null>(null);
  let generation = 0;
  const api = createApiClient({
    baseUrl: API_BASE_URL,
    getAccessToken: () => window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  });

  async function refresh(): Promise<void> {
    const current = generation;
    try {
      const result = await api.request<VerificationPlanListResponse>("/aiops/verifications");
      if (current === generation && !isSaving.value) plans.value = result.items;
    } catch {
      if (current === generation) plans.value = [];
    }
  }

  async function save(path: string, body?: unknown): Promise<void> {
    if (isSaving.value) return;
    const current = ++generation;
    isSaving.value = true;
    errorMessage.value = null;
    try {
      const plan = await api.request<VerificationPlan>(path, {
        method: "POST",
        ...(body === undefined ? {} : { body: JSON.stringify(body) })
      });
      if (current === generation) plans.value = [plan, ...plans.value.filter((item) => item.id !== plan.id)];
    } catch (error) {
      if (current === generation) errorMessage.value = toUserFacingError(error);
    } finally {
      if (current === generation) isSaving.value = false;
    }
  }

  function reset(): void {
    generation += 1;
    plans.value = [];
    isSaving.value = false;
    errorMessage.value = null;
  }

  return {
    plans, isSaving, errorMessage, refresh, reset,
    request: (diagnosticId: string) => save(`/aiops/diagnostics/${diagnosticId}/verification`),
    decide: (plan: VerificationPlan, action: "approve" | "reject" | "cancel") => save(
      `/aiops/verifications/${plan.id}:decide`, { action, templateVersion: plan.templateVersion }
    )
  };
});
