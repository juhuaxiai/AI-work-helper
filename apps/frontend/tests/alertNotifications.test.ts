// @vitest-environment jsdom

import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, expect, it, vi } from "vitest";

import type { AlertNotification } from "@agent-py/api-contracts";

import AlertNotificationHistory from "../src/components/AlertNotificationHistory.vue";
import { useAlertNotificationsStore } from "../src/stores/alertNotifications";

const notification: AlertNotification = {
  id: "alert-test", source: "local", alertName: "LocalTtsLatencyHigh",
  service: "tts-demo", environment: "test", status: "firing",
  startsAt: "2026-11-22T16:32:00Z", receivedAt: "2026-11-22T16:32:03Z",
  diagnosticId: "diagnostic-test"
};

afterEach(() => vi.restoreAllMocks());

it("updates the firing count and ignores old responses after identity reset", async () => {
  setActivePinia(createPinia());
  const answered = (item: AlertNotification): Response =>
    new Response(JSON.stringify({ ok: true, data: { items: [item] } }));
  const fetcher = vi.spyOn(globalThis, "fetch");
  fetcher.mockResolvedValueOnce(answered(notification));
  fetcher.mockResolvedValueOnce(answered({ ...notification, status: "resolved" }));
  const store = useAlertNotificationsStore();
  await store.refresh();
  expect(store.activeCount).toBe(1);
  await store.refresh();
  expect(store.activeCount).toBe(0);
  expect(store.items).toHaveLength(1);

  let answer: (result: Response) => void = () => undefined;
  fetcher.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
  const pending = store.refresh();
  store.reset();
  answer(answered(notification));
  await pending;
  expect(store.items).toHaveLength(0);
});

it("shows resolution and links the authorized diagnostic", async () => {
  const wrapper = mount(AlertNotificationHistory, {
    props: { items: [{ ...notification, status: "resolved" }] }
  });
  expect(wrapper.text()).toContain("已解除");
  await wrapper.get("button").trigger("click");
  expect(wrapper.emitted("select")).toEqual([["diagnostic-test"]]);
});
