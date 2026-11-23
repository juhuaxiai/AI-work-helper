<script setup lang="ts">
import type { VerificationPlan, VerificationStatus, VerificationTemplate } from "@agent-py/api-contracts";

defineProps<{
  readonly plans: readonly VerificationPlan[];
  readonly canRequest: boolean;
  readonly isSaving: boolean;
  readonly errorMessage: string | null;
}>();
const emit = defineEmits<{
  request: [];
  decide: [plan: VerificationPlan, action: "approve" | "reject" | "cancel"];
}>();

const titles: Record<VerificationTemplate, string> = {
  "tts-queue-concurrency": "TTS 排队并发对照",
  "tts-synthesis-baseline": "TTS 合成耗时对照"
};
const hypotheses: Record<VerificationTemplate, string> = {
  "tts-queue-concurrency": "增加消费并发会缩短测试请求的排队时间。",
  "tts-synthesis-baseline": "合成阶段的额外等待能解释本次测试中的延迟。"
};
const statuses: Record<VerificationStatus, string> = {
  awaiting_approval: "待审批", queued: "已入队", running: "验证中", succeeded: "验证完成",
  failed: "执行失败", rejected: "已拒绝", expired: "已过期", cancelled: "已取消"
};
const displayTime = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"
});
</script>

<template>
  <section class="aiops-verification" aria-label="沙箱验证">
    <header>
      <h3>沙箱验证</h3>
      <button v-if="canRequest" type="button" :disabled="isSaving" @click="emit('request')">申请验证</button>
    </header>
    <p v-if="errorMessage" role="alert">{{ errorMessage }}</p>
    <p v-if="plans.length === 0">暂无验证计划</p>
    <details v-for="plan in plans" :key="plan.id" :open="plan.status === 'awaiting_approval'">
      <summary>{{ titles[plan.template] }} · {{ statuses[plan.status] }}</summary>
      <dl>
        <div><dt>测试目标</dt><dd>容器内 TTS 对照 · test</dd></div>
        <div><dt>待验证假设</dt><dd>{{ plan.hypothesis ?? hypotheses[plan.template] }}</dd></div>
        <div v-if="plan.selectionReason"><dt>选择依据</dt><dd>{{ plan.selectionReason }}</dd></div>
        <div><dt>限制</dt><dd>无外部网络 · 0.5 CPU · 96 MiB</dd></div>
        <div><dt>证据 Trace</dt><dd><code>{{ plan.traceId }}</code></dd></div>
        <div><dt>镜像</dt><dd><code>{{ plan.imageId }}</code></dd></div>
        <div><dt>模板版本</dt><dd>{{ plan.templateVersion }}</dd></div>
        <div><dt>执行编号</dt><dd><code>{{ plan.executionId }}</code></dd></div>
        <div><dt>有效期</dt><dd>{{ displayTime.format(new Date(plan.expiresAt)) }}</dd></div>
        <div v-if="plan.status === 'succeeded'">
          <dt>实测对照</dt>
          <dd>{{ plan.result.controlMs }} ms → {{ plan.result.comparisonMs }} ms</dd>
        </div>
        <div v-if="plan.status === 'failed' && typeof plan.result.reason === 'string'"><dt>原因</dt><dd>{{ plan.result.reason }}</dd></div>
      </dl>
      <div class="aiops-verification__actions">
        <template v-if="plan.status === 'awaiting_approval'">
          <button type="button" :disabled="isSaving" @click="emit('decide', plan, 'approve')">批准</button>
          <button type="button" :disabled="isSaving" @click="emit('decide', plan, 'reject')">拒绝</button>
        </template>
        <button v-if="plan.status === 'queued' || plan.status === 'running'" type="button" :disabled="isSaving" @click="emit('decide', plan, 'cancel')">取消验证</button>
      </div>
    </details>
  </section>
</template>

<style scoped>
.aiops-verification { border: 1px solid var(--line); border-radius: var(--radius-md); min-width: 0; padding: .8rem; }
header, .aiops-verification__actions { align-items: center; display: flex; flex-wrap: wrap; gap: .45rem; justify-content: space-between; }
h3 { font-size: .9rem; margin: 0; }
p, dl { color: var(--text-secondary); font-size: .8rem; }
details { border-top: 1px solid var(--line); margin-top: .55rem; padding-top: .55rem; }
summary { cursor: pointer; font-size: .83rem; font-weight: 650; }
dl, dl > div { display: grid; gap: .25rem; }
dl { gap: .45rem; }
dt { font-weight: 600; }
dd { margin: 0; overflow-wrap: anywhere; }
button { border: 1px solid var(--line-strong); border-radius: var(--radius-sm); font-size: .78rem; padding: .35rem .55rem; }
button:disabled { opacity: .65; }
</style>
