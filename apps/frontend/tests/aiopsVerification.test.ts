// @vitest-environment jsdom

import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";

import type { VerificationPlan } from "@agent-py/api-contracts";

import AiopsVerificationPanel from "../src/components/AiopsVerificationPanel.vue";

const plan: VerificationPlan = {
  id: "verify-test", diagnosticId: "diagnostic-test", template: "tts-queue-concurrency",
  templateVersion: 1, status: "awaiting_approval", traceId: "e".repeat(32),
  evidenceId: "reference-test", imageId: "sha256:" + "a".repeat(64),
  executionId: "d".repeat(32), expiresAt: "2026-11-23T05:00:00Z",
  updatedAt: "2026-11-23T04:30:00Z", result: {}
};

it("shows the approval scope and the measured result", async () => {
  const wrapper = mount(AiopsVerificationPanel, {
    props: { plans: [plan], canRequest: false, isSaving: false, errorMessage: null }
  });
  expect(wrapper.text()).toContain("无外部网络");
  expect(wrapper.text()).toContain(plan.traceId);
  await wrapper.get("button").trigger("click");
  expect(wrapper.emitted("decide")).toEqual([[plan, "approve"]]);
  await wrapper.setProps({
    plans: [{ ...plan, status: "succeeded", result: { controlMs: 280, comparisonMs: 80 } }]
  });
  expect(wrapper.text()).toContain("280 ms → 80 ms");
  expect(wrapper.find("button").exists()).toBe(false);
});
